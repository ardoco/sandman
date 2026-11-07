use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use bytes::Bytes;
use sandman_agent::auth::{
    ForbiddenError, TokenVerifier, actor_from_metadata, authenticator_for, bearer_token,
};
use sandman_agent::config::{default_contracts_dir, llm_mode, process_env, settings_from_env};
use sandman_agent::contracts::Contracts;
use sandman_agent::events::{
    CONSUMER_BACKOFF, Delivery, Event, EventPublisher, EventSink, Handler, JetPublish,
    PublishOptions, build_event, handle_message,
};
use sandman_agent::ids::sha256_hex;
use sandman_agent::kg::{
    KgBackend, KgClient, KgError, MEMORY_LIMIT_CODE, Mode, Params, Row, RunRecorder, RunStart,
    Statement, classify_code,
};
use sandman_agent::llm::{ChatRequest, LlmClient, LlmError};
use sandman_agent::status::StatusTracker;
use serde_json::{Map, Value, json};

fn contracts() -> Arc<Contracts> {
    Arc::new(Contracts::new(&default_contracts_dir()).unwrap())
}

fn object(v: Value) -> Map<String, Value> {
    v.as_object().unwrap().clone()
}

fn valid_data() -> Map<String, Value> {
    object(json!({
        "projectId": "mediastore",
        "sourceVersionIds": ["ver_0123456789abcdef0123456789abcdef"],
        "targetVersionIds": ["ver_fedcba9876543210fedcba9876543210"],
        "runs": [{"approach": "lissa", "approachVersion": "1.0.0", "runId": "run_0123456789abcdef0123456789abcdef",
                  "status": "completed", "linkCount": 3}],
    }))
}

fn temp_file(name: &str, content: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sandman-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    std::fs::write(&path, content).unwrap();
    path
}

fn env(pairs: &[(&str, &str)]) -> HashMap<String, String> {
    pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

type FailFn = Box<dyn Fn(&str) -> Option<KgError> + Send>;

#[derive(Default)]
struct FakeBackend {
    modes: Mutex<Vec<Mode>>,
    statements: Mutex<Vec<(String, Params)>>,
    attempts: Mutex<u32>,
    fail: Mutex<Option<FailFn>>,
}

impl FakeBackend {
    fn failing(f: impl Fn(&str) -> Option<KgError> + Send + 'static) -> Self {
        let b = Self::default();
        *b.fail.lock().unwrap() = Some(Box::new(f));
        b
    }
    fn check(&self, cypher: &str, params: &Params) -> Result<(), KgError> {
        *self.attempts.lock().unwrap() += 1;
        self.statements
            .lock()
            .unwrap()
            .push((cypher.to_string(), params.clone()));
        match self.fail.lock().unwrap().as_ref().and_then(|f| f(cypher)) {
            Some(e) => Err(e),
            None => Ok(()),
        }
    }
}

#[async_trait]
impl KgBackend for &FakeBackend {
    async fn transaction(
        &self,
        mode: Mode,
        statements: &[Statement],
    ) -> Result<Vec<Vec<Row>>, KgError> {
        self.modes.lock().unwrap().push(mode);
        let mut out = Vec::new();
        for (c, p) in statements {
            self.check(c, p)?;
            out.push(vec![object(json!({"n": 1}))]);
        }
        Ok(out)
    }
    async fn autocommit(&self, cypher: &str, params: &Params) -> Result<Vec<Row>, KgError> {
        self.check(cypher, params)?;
        Ok(vec![object(json!({"n": 1}))])
    }
}

fn kg(b: &FakeBackend) -> KgClient<&FakeBackend> {
    KgClient::new(b).with_backoff(vec![Duration::ZERO, Duration::ZERO])
}

#[tokio::test]
async fn kg_read_uses_a_read_transaction_and_returns_rows() {
    let b = FakeBackend::default();
    let rows = kg(&b)
        .read("MATCH (n) RETURN n", Params::new())
        .await
        .unwrap();
    assert_eq!(rows, vec![object(json!({"n": 1}))]);
    assert_eq!(*b.modes.lock().unwrap(), vec![Mode::Read]);
}

#[tokio::test]
async fn kg_does_not_retry_the_memory_limit_error() {
    let b = FakeBackend::failing(|_| Some(classify_code(MEMORY_LIMIT_CODE, "limit")));
    let err = kg(&b)
        .read("MATCH (n) RETURN n", Params::new())
        .await
        .unwrap_err();
    assert!(matches!(err, KgError::MemoryLimit(_)));
    assert_eq!(*b.attempts.lock().unwrap(), 1);
}

#[tokio::test]
async fn kg_retries_other_transient_errors() {
    let b = FakeBackend::failing(|_| {
        Some(classify_code(
            "Neo.TransientError.Transaction.DeadlockDetected",
            "deadlock",
        ))
    });
    let err = kg(&b)
        .write("MATCH (n) RETURN n", Params::new())
        .await
        .unwrap_err();
    assert_eq!(err, KgError::Transient("deadlock".into()));
    assert_eq!(*b.attempts.lock().unwrap(), 3);
    let b = FakeBackend::failing(|_| {
        Some(classify_code(
            "Neo.ClientError.Statement.SyntaxError",
            "syntax",
        ))
    });
    kg(&b).write("x", Params::new()).await.unwrap_err();
    assert_eq!(*b.attempts.lock().unwrap(), 1);
}

#[tokio::test]
async fn kg_init_schema_skips_comments_and_ignores_already_exists() {
    let b = FakeBackend::failing(|c| {
        c.contains("idx_a")
            .then(|| KgError::Other("Index already exists".into()))
    });
    let file = temp_file(
        "init.cypher",
        "// comment\nCREATE INDEX idx_a FOR (n:A) ON (n.id);\nCREATE INDEX idx_b FOR (n:B) ON (n.id);\n",
    );
    kg(&b).init_schema(&file).await.unwrap();
    let seen: Vec<String> = b
        .statements
        .lock()
        .unwrap()
        .iter()
        .map(|s| s.0.chars().take(20).collect())
        .collect();
    assert_eq!(seen, ["CREATE INDEX idx_a F", "CREATE INDEX idx_b F"]);
    let b = FakeBackend::failing(|_| Some(KgError::Other("syntax error".into())));
    assert_eq!(
        kg(&b).init_schema(&file).await.unwrap_err().to_string(),
        "syntax error"
    );
}

#[tokio::test]
async fn run_recorder_passes_token_counts_and_omits_unset_model() {
    let b = FakeBackend::default();
    let recorder = RunRecorder::new(kg(&b), "tlr-agent", "1").with_env(HashMap::new());
    recorder
        .complete(
            "run_x",
            Some(sandman_agent::llm::Usage {
                input_tokens: 7,
                output_tokens: 3,
            }),
            "completed",
            None,
            false,
        )
        .await
        .unwrap();
    let params = b.statements.lock().unwrap()[0].1.clone();
    assert_eq!((&params["inp"], &params["out"]), (&json!(7), &json!(3)));
    let start = RunStart {
        project_id: "p".into(),
        skill: "s".into(),
        tool: "t".into(),
        tool_version: "1".into(),
        input_key: "k".into(),
        ..Default::default()
    };
    let id = recorder.start(start).await.unwrap();
    assert!(id.starts_with("run_") && id.len() == 36);
    let props = b.statements.lock().unwrap()[1].1["props"].clone();
    assert_eq!(props["id"], json!(id));
    assert_eq!(
        (&props["status"], &props["replayMode"]),
        (&json!("running"), &json!("live"))
    );
    assert!(props.get("model").is_none());
}

#[test]
fn auth_maps_bearer_tokens_to_callers() {
    let hashes = env(&[
        ("orchestrator", &sha256_hex("secret")),
        ("plugin:x", &sha256_hex("p")),
    ]);
    let v = TokenVerifier::new(&hashes);
    assert_eq!(
        v.caller_from_header(Some("Bearer secret")).as_deref(),
        Some("orchestrator")
    );
    assert_eq!(
        v.caller_from_header(Some("bearer secret")).as_deref(),
        Some("orchestrator")
    );
    assert_eq!(v.caller_from_header(Some("Bearer wrong")), None);
    assert_eq!(v.caller_from_header(Some("secret")), None);
    assert_eq!(v.caller_from_header(None), None);
}

#[test]
fn auth_rejects_plugin_callers_on_agents_and_accepts_the_inbound_token_on_plugins() {
    let hashes = env(&[
        ("orchestrator", &sha256_hex("secret")),
        ("plugin:x", &sha256_hex("p")),
    ]);
    let agent = authenticator_for(None, &hashes);
    assert_eq!(
        agent(Some("Bearer secret")).as_deref(),
        Some("orchestrator")
    );
    assert_eq!(agent(Some("Bearer p")), None);
    let plugin = authenticator_for(Some("in"), &HashMap::new());
    assert_eq!(plugin(Some("Bearer in")).as_deref(), Some("owner"));
    assert_eq!(plugin(Some("Bearer out")), None);
}

#[test]
fn auth_accepts_actor_only_from_the_orchestrator() {
    let with_actor = object(json!({"sandman.actor": "alice"}));
    assert_eq!(
        actor_from_metadata("orchestrator", &with_actor)
            .unwrap()
            .as_deref(),
        Some("alice")
    );
    assert_eq!(actor_from_metadata("x", &Map::new()).unwrap(), None);
    assert!(matches!(
        actor_from_metadata("query-service", &with_actor),
        Err(ForbiddenError(_))
    ));
}

#[tokio::test]
async fn status_reports_working_while_a_task_runs() {
    let t = StatusTracker::with_version("svc", "1");
    assert_eq!(t.snapshot(None)["state"], "idle");
    let during = t.track(async { t.snapshot(None) }).await;
    assert_eq!(
        (&during["state"], &during["activeTasks"]),
        (&json!("working"), &json!(1))
    );
    let after = t.snapshot(None);
    assert_eq!(
        (&after["state"], &after["activeTasks"]),
        (&json!("idle"), &json!(0))
    );
    assert!(after["lastActivityAt"].is_string());
}

#[test]
fn config_reads_settings_and_file_secrets() {
    let token = temp_file("token", "s3cret\n");
    let s = settings_from_env(&env(&[
        ("SANDMAN_SERVICE_NAME", "svc"),
        ("SANDMAN_SERVICE_TOKEN_FILE", token.to_str().unwrap()),
        ("SANDMAN_KG_URI", "bolt://kg"),
    ]))
    .unwrap();
    assert_eq!(s.service_name, "svc");
    assert_eq!(s.service_token.as_deref(), Some("s3cret"));
    assert_eq!(s.kg_uri.as_deref(), Some("bolt://kg"));
    assert!(s.service_verify.is_empty());
}

#[test]
fn config_fails_on_missing_name_unreadable_file_bad_mode_and_missing_required() {
    assert!(settings_from_env(&HashMap::new()).is_err());
    assert!(
        settings_from_env(&env(&[
            ("SANDMAN_SERVICE_NAME", "a"),
            ("SANDMAN_KG_PASSWORD_FILE", "/nonexistent")
        ]))
        .is_err()
    );
    assert!(llm_mode(&env(&[("SANDMAN_LLM_MODE", "x")])).is_err());
    assert_eq!(llm_mode(&HashMap::new()).unwrap(), "live");
    let s = settings_from_env(&env(&[("SANDMAN_SERVICE_NAME", "a")])).unwrap();
    assert_eq!(
        s.require(&["SANDMAN_KG_URI", "SANDMAN_NATS_URL"])
            .unwrap_err()
            .to_string(),
        "a: missing configuration SANDMAN_KG_URI, SANDMAN_NATS_URL"
    );
}

#[derive(Clone, Default)]
struct FakeJet {
    calls: Arc<Mutex<Vec<(String, String)>>>,
    failures: Arc<Mutex<u32>>,
    always_fail: bool,
}

#[async_trait]
impl JetPublish for FakeJet {
    async fn publish(&self, subject: &str, _payload: Bytes, msg_id: &str) -> Result<(), String> {
        self.calls
            .lock()
            .unwrap()
            .push((subject.into(), msg_id.into()));
        let mut failures = self.failures.lock().unwrap();
        if self.always_fail || *failures > 0 {
            *failures = failures.saturating_sub(1);
            return Err("down".into());
        }
        Ok(())
    }
}

fn publisher(js: FakeJet) -> EventPublisher<FakeJet> {
    EventPublisher::new(js, contracts(), "tlr-agent").with_backoff(vec![Duration::ZERO; 3])
}

#[tokio::test]
async fn events_publish_with_msg_id_retry_then_fail() {
    let js = FakeJet {
        failures: Arc::new(Mutex::new(2)),
        ..Default::default()
    };
    let options = PublishOptions {
        causation_id: Some("task-1".into()),
        ..Default::default()
    };
    let event = publisher(js.clone())
        .publish(
            "sandman.tracelinks.updated",
            "mediastore",
            valid_data(),
            options.clone(),
        )
        .await
        .unwrap();
    {
        let calls = js.calls.lock().unwrap();
        assert_eq!(calls.len(), 3);
        assert_eq!(calls[0].0, "sandman.tracelinks.updated");
        assert_eq!(calls[2].1, event.id);
    }
    let down = FakeJet {
        always_fail: true,
        ..Default::default()
    };
    let err = publisher(down.clone())
        .publish(
            "sandman.tracelinks.updated",
            "mediastore",
            valid_data(),
            options,
        )
        .await
        .unwrap_err();
    assert!(err.to_string().contains("failed after 4 attempts"));
    assert_eq!(down.calls.lock().unwrap().len(), 4);
}

#[test]
fn events_reject_an_invalid_envelope_before_publishing() {
    let c = contracts();
    let bad = object(json!({"projectId": "mediastore"}));
    assert!(
        build_event(
            &c,
            "tlr-agent",
            "sandman.tracelinks.updated",
            "mediastore",
            bad,
            PublishOptions::default()
        )
        .is_err()
    );
}

struct FakeDelivery {
    data: Vec<u8>,
    count: u64,
    log: Arc<Mutex<Vec<String>>>,
}

#[async_trait]
impl Delivery for FakeDelivery {
    fn data(&self) -> &[u8] {
        &self.data
    }
    fn delivery_count(&self) -> u64 {
        self.count
    }
    async fn ack(&mut self) {
        self.log.lock().unwrap().push("ack".into());
    }
    async fn nak(&mut self, delay: Duration) {
        self.log
            .lock()
            .unwrap()
            .push(format!("nak {}", delay.as_secs()));
    }
}

fn delivery(count: u64) -> (FakeDelivery, Arc<Mutex<Vec<String>>>) {
    let event = json!({
        "specversion": "1.0", "id": "0b6f4a0e-8f6e-4c6b-9d7a-1f2e3d4c5b6a", "source": "sandman/x", "type": "sandman.x",
        "subject": "p", "time": "2026-10-06T12:00:00Z", "datacontenttype": "application/json", "data": {},
    });
    let log = Arc::new(Mutex::new(Vec::new()));
    (
        FakeDelivery {
            data: event.to_string().into_bytes(),
            count,
            log: log.clone(),
        },
        log,
    )
}

#[tokio::test]
async fn events_ack_after_success_and_nak_with_backoff_on_failure() {
    let ok: Handler = Box::new(|_e: Event| Box::pin(async { Ok(()) }));
    let (mut d, log) = delivery(1);
    handle_message(&mut d, &ok).await;
    assert_eq!(*log.lock().unwrap(), ["ack"]);
    let bad: Handler = Box::new(|_e: Event| Box::pin(async { Err("boom".to_string()) }));
    let (mut d, log) = delivery(2);
    handle_message(&mut d, &bad).await;
    assert_eq!(
        *log.lock().unwrap(),
        [format!("nak {}", CONSUMER_BACKOFF[1])]
    );
}

async fn gateway(status: u16, body: Value) -> String {
    let app = axum::Router::new().fallback(move || {
        let body = body.clone();
        async move {
            (
                axum::http::StatusCode::from_u16(status).unwrap(),
                axum::Json(body),
            )
        }
    });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    url
}

fn routes() -> HashMap<String, String> {
    env(&[("chat-default", "m")])
}

fn chat_request() -> ChatRequest {
    ChatRequest {
        model: "chat-default".into(),
        messages: vec![],
        ..Default::default()
    }
}

#[tokio::test]
async fn llm_maps_http_402_to_budget_exceeded() {
    let url = gateway(402, json!({"error": "budget"})).await;
    let err = LlmClient::new(&url, "k", routes(), false)
        .chat(&chat_request())
        .await
        .unwrap_err();
    assert!(matches!(err, LlmError::BudgetExceeded(_)));
}

#[tokio::test]
async fn llm_enforces_the_local_only_policy_and_unknown_routes() {
    let client = LlmClient::new("http://127.0.0.1:1", "k", routes(), true);
    assert!(matches!(
        client.chat(&chat_request()).await,
        Err(LlmError::Policy(_))
    ));
    assert!(matches!(
        client.embed("nope-local", &["x"]).await,
        Err(LlmError::Policy(_))
    ));
}

#[tokio::test]
async fn llm_returns_content_usage_and_resolved_model() {
    let url = gateway(
        200,
        json!({"model": "resolved", "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 4, "completion_tokens": 2}}),
    )
    .await;
    let r = LlmClient::new(&url, "k", routes(), false)
        .chat(&chat_request())
        .await
        .unwrap();
    assert_eq!(
        (
            r.message.content.as_str(),
            r.finish_reason.as_str(),
            r.resolved_model.as_str()
        ),
        ("hi", "stop", "resolved")
    );
    assert_eq!((r.usage.input_tokens, r.usage.output_tokens), (4, 2));
}

#[test]
fn generated_workflow_ids_are_prefixed_and_unique() {
    let a = sandman_agent::ids::new_workflow_id();
    let b = sandman_agent::ids::new_workflow_id();
    assert!(a.starts_with("wf_"));
    assert_eq!(a.len(), 3 + 32);
    assert!(a[3..].chars().all(|c| c.is_ascii_hexdigit()));
    assert_ne!(a, b);
}

#[test]
fn bearer_token_accepts_only_the_bearer_scheme() {
    assert_eq!(bearer_token(Some("Bearer  abc ")), Some("abc"));
    assert_eq!(bearer_token(Some("Basic abcdefgh")), None);
    assert_eq!(bearer_token(Some("Bearer\u{20ac}x")), None);
    assert_eq!(bearer_token(Some("Bearer ")), None);
    assert_eq!(bearer_token(Some("Bear")), None);
}

#[test]
fn forbidden_error_displays_its_message() {
    assert_eq!(ForbiddenError("nope".into()).to_string(), "nope");
}

#[test]
fn config_require_accepts_every_configured_name_and_flags_each_missing_one() {
    let verify = temp_file("verify", r#"{"orchestrator": "abc"}"#);
    let pairs = [
        ("SANDMAN_LLM_BASE_URL", "u"),
        ("SANDMAN_LLM_KEY", "k"),
        ("SANDMAN_LLM_LOCAL_KEY", "l"),
        ("SANDMAN_KG_URI", "bolt://kg"),
        ("SANDMAN_KG_USER", "neo"),
        ("SANDMAN_KG_PASSWORD", "pw"),
        ("SANDMAN_NATS_URL", "nats://n"),
        ("SANDMAN_SERVICE_TOKEN", "t"),
        ("SANDMAN_PLUGIN_INBOUND_TOKEN", "in"),
    ];
    let mut all: Vec<(&str, &str)> = pairs.to_vec();
    all.push(("SANDMAN_SERVICE_NAME", "svc"));
    all.push(("SANDMAN_SERVICE_VERIFY_FILE", verify.to_str().unwrap()));
    let full = settings_from_env(&env(&all)).unwrap();
    let mut names: Vec<&str> = pairs.iter().map(|(n, _)| *n).collect();
    names.push("SANDMAN_SERVICE_VERIFY");
    assert!(full.require(&names).is_ok());
    let bare = settings_from_env(&env(&[("SANDMAN_SERVICE_NAME", "svc")])).unwrap();
    for name in &names {
        assert!(bare.require(&[name]).is_err(), "{name}");
    }
    assert!(full.require(&["SANDMAN_UNKNOWN"]).is_err());
}

#[test]
fn process_env_reads_the_process_environment() {
    assert!(process_env().contains_key("CARGO_PKG_NAME"));
}
