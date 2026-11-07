use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use sandman_agent::a2a::{
    INVOCATION_EXTENSION, ServeOptions, Skill, SkillContext, SkillFailure, SkillResult, serve,
};
use sandman_agent::config::{Settings, default_contracts_dir};
use sandman_agent::contracts::Contracts;
use sandman_agent::events::{Event, EventSink, PublishError, PublishOptions, build_event};
use sandman_agent::ids::sha256_hex;
use serde_json::{Map, Value, json};

struct Sink {
    contracts: Arc<Contracts>,
    published: Mutex<Vec<Event>>,
}

#[async_trait]
impl EventSink for Sink {
    async fn publish(
        &self,
        r#type: &str,
        subject: &str,
        data: Map<String, Value>,
        options: PublishOptions,
    ) -> Result<Event, PublishError> {
        let event = build_event(&self.contracts, "tlr-agent", r#type, subject, data, options)
            .map_err(PublishError)?;
        self.published.lock().unwrap().push(event.clone());
        Ok(event)
    }
}

struct RecoverSkill(Arc<AtomicUsize>);

#[async_trait]
impl Skill for RecoverSkill {
    fn id(&self) -> &str {
        "recover-trace-links"
    }
    async fn run(
        &self,
        _ctx: &SkillContext,
        input: &Map<String, Value>,
    ) -> Result<SkillResult, SkillFailure> {
        self.0.fetch_add(1, Ordering::SeqCst);
        let run = json!({"approach": "lissa", "approachVersion": "1.0.0",
            "runId": "run_0123456789abcdef0123456789abcdef", "status": "completed", "linkCount": 0});
        let data = json!({
            "projectId": input["projectId"], "sourceVersionIds": input["sourceVersionIds"],
            "targetVersionIds": input["targetVersionIds"], "runs": [run],
        });
        Ok(SkillResult {
            output: json!({"runs": [run]}).as_object().unwrap().clone(),
            event: Some((
                "sandman.tracelinks.updated".into(),
                data.as_object().unwrap().clone(),
            )),
        })
    }
}

struct Harness {
    base: String,
    runs: Arc<AtomicUsize>,
    sink: Arc<Sink>,
    cases: Vec<Value>,
    http: reqwest::Client,
}

async fn harness() -> Harness {
    let dir = default_contracts_dir();
    let vectors: Value = serde_json::from_str(
        &std::fs::read_to_string(dir.join("conformance/vectors.json")).unwrap(),
    )
    .unwrap();
    let contracts = Arc::new(Contracts::new(&dir).unwrap());
    let sink = Arc::new(Sink {
        contracts: contracts.clone(),
        published: Mutex::new(Vec::new()),
    });
    let runs = Arc::new(AtomicUsize::new(0));
    let settings = Settings {
        service_name: "tlr-agent".into(),
        contracts_dir: dir.clone(),
        service_verify: HashMap::from([("orchestrator".to_string(), sha256_hex("secret"))]),
        ..Default::default()
    };
    let app = serve(ServeOptions {
        agent_card_path: dir.join("agent-cards/tlr-agent.json"),
        skills: vec![Arc::new(RecoverSkill(runs.clone()))],
        settings,
        events: Some(sink.clone()),
        contracts: Some(contracts),
        status: None,
    })
    .unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let cases = vectors["skillInvocation"]["cases"]
        .as_array()
        .unwrap()
        .clone();
    Harness {
        base,
        runs,
        sink,
        cases,
        http: reqwest::Client::new(),
    }
}

impl Harness {
    async fn send(&self, message: &Value, token: Option<&str>) -> reqwest::Response {
        let mut request = self
            .http
            .post(format!("{}/a2a", self.base))
            .header("A2A-Version", "1.0")
            .header("A2A-Extensions", INVOCATION_EXTENSION)
            .json(&json!({"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": message}}));
        if let Some(t) = token {
            request = request.bearer_auth(t);
        }
        request.send().await.unwrap()
    }

    async fn task(&self, message: &Value) -> Value {
        let body: Value = self
            .send(message, Some("secret"))
            .await
            .json()
            .await
            .unwrap();
        assert!(body.get("error").is_none(), "{body}");
        body["result"]
            .get("task")
            .unwrap_or(&body["result"])
            .clone()
    }

    fn valid_case(&self) -> Value {
        self.cases
            .iter()
            .find(|c| c["name"] == "valid input is accepted")
            .unwrap()["message"]
            .clone()
    }
}

#[tokio::test]
async fn rejects_invalid_invocations_from_the_vectors() {
    let h = harness().await;
    for c in h
        .cases
        .iter()
        .filter(|c| c["expectedState"] == "TASK_STATE_REJECTED")
    {
        let task = h.task(&c["message"]).await;
        assert_eq!(
            task["status"]["state"], "TASK_STATE_REJECTED",
            "{}",
            c["name"]
        );
        assert_eq!(
            task["status"]["message"]["metadata"]["sandman.error"]["code"], c["expectedErrorCode"],
            "{}",
            c["name"]
        );
    }
}

#[tokio::test]
async fn completes_a_valid_invocation_with_one_result_artifact_and_one_event() {
    let h = harness().await;
    let task = h.task(&h.valid_case()).await;
    assert_eq!(task["status"]["state"], "TASK_STATE_COMPLETED");
    assert_eq!(task["artifacts"][0]["name"], "result");
    let published = h.sink.published.lock().unwrap();
    assert_eq!(published.len(), 1);
    assert_eq!(published[0].r#type, "sandman.tracelinks.updated");
}

#[tokio::test]
async fn answers_a_repeated_message_id_without_running_the_skill_again() {
    let h = harness().await;
    let mut message = h.valid_case();
    message["messageId"] = json!("m-repeat");
    let first = h.task(&message).await;
    h.task(&message).await;
    assert_eq!(first["status"]["state"], "TASK_STATE_COMPLETED");
    assert_eq!(h.runs.load(Ordering::SeqCst), 1);
    assert_eq!(h.sink.published.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn rejects_requests_without_a_valid_token() {
    let h = harness().await;
    let message = &h.cases[0]["message"];
    assert_eq!(h.send(message, None).await.status(), 401);
    assert_eq!(h.send(message, Some("wrong")).await.status(), 401);
}

#[tokio::test]
async fn serves_the_agent_card_and_the_status_route() {
    let h = harness().await;
    let card: Value = h
        .http
        .get(format!("{}/.well-known/agent-card.json", h.base))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(card["name"], "sandman-tlr-agent");
    let status = h
        .http
        .get(format!("{}/v1/status", h.base))
        .bearer_auth("secret")
        .send()
        .await
        .unwrap();
    assert_eq!(status.status(), 200);
    assert_eq!(
        h.http
            .get(format!("{}/v1/status", h.base))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
}
