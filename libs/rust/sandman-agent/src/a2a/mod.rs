mod protocol;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use async_trait::async_trait;
use axum::Router;
use axum::body::Bytes;
use axum::extract::State;
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use serde_json::{Map, Value, json};
use tokio::sync::OnceCell;

use crate::auth::{Authenticator, actor_from_metadata, authenticator_for};
use crate::config::Settings;
use crate::contracts::Contracts;
use crate::events::{EventSink, PublishOptions};
use crate::ids::{SEP, canonical_json};
use crate::kg::KgError;
use crate::llm::LlmError;
use crate::status::StatusTracker;
use crate::telemetry::with_span;

pub use protocol::Invocation;

pub const SKILL_KEY: &str = "sandman.skill";
pub const WORKFLOW_KEY: &str = "sandman.workflowId";
pub const ACTOR_KEY: &str = "sandman.actor";
pub const ERROR_KEY: &str = "sandman.error";
pub const INVOCATION_EXTENSION: &str = "https://sandman.invalid/a2a/extensions/skill-invocation/v1";
const MAX_SEEN: usize = 1024;
const MAX_TASKS: usize = 1024;
const ACTIVITY_INTERVAL: Duration = Duration::from_secs(2);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SkillError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
}

impl SkillError {
    pub fn new(code: &str, message: impl Into<String>, retryable: bool) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            retryable,
        }
    }

    pub fn to_json(&self) -> Value {
        json!({"code": self.code, "message": self.message, "retryable": self.retryable})
    }
}

impl std::fmt::Display for SkillError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

/// Failure of a skill run. Budget errors map to `BUDGET_EXCEEDED`, other errors to a retryable `INTERNAL`.
#[derive(Debug)]
pub enum SkillFailure {
    Skill(SkillError),
    Budget(String),
    Other(String),
}

impl From<SkillError> for SkillFailure {
    fn from(e: SkillError) -> Self {
        Self::Skill(e)
    }
}
impl From<LlmError> for SkillFailure {
    fn from(e: LlmError) -> Self {
        match e {
            LlmError::BudgetExceeded(m) => Self::Budget(m),
            other => Self::Other(other.to_string()),
        }
    }
}
impl From<KgError> for SkillFailure {
    fn from(e: KgError) -> Self {
        Self::Other(e.to_string())
    }
}

#[derive(Debug, Clone)]
pub struct SkillResult {
    pub output: Map<String, Value>,
    pub event: Option<(String, Map<String, Value>)>,
}

#[derive(Clone)]
pub struct SkillContext {
    pub task_id: String,
    pub message_id: String,
    pub workflow_id: Option<String>,
    pub caller: String,
    pub actor: Option<String>,
    pub settings: Arc<Settings>,
    progress: Arc<dyn Fn(&str) + Send + Sync>,
}

impl SkillContext {
    pub fn progress(&self, text: &str) {
        (self.progress)(text);
    }
}

#[async_trait]
pub trait Skill: Send + Sync {
    fn id(&self) -> &str;
    async fn run(
        &self,
        ctx: &SkillContext,
        input: &Map<String, Value>,
    ) -> Result<SkillResult, SkillFailure>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TaskState {
    Submitted,
    Working,
    Completed,
    Failed,
    Rejected,
}

#[derive(Debug, Clone)]
pub struct TaskView {
    pub id: String,
    pub context_id: String,
    pub state: TaskState,
    pub error: Option<SkillError>,
    pub message_text: Option<String>,
    pub output: Option<Value>,
}

#[derive(Clone)]
struct Outcome {
    output: Option<Map<String, Value>>,
    error: Option<SkillError>,
    reject: bool,
}

struct SeenEntry {
    fingerprint: String,
    done: Arc<OnceCell<Outcome>>,
}

#[derive(Default)]
struct TaskStore {
    tasks: HashMap<String, TaskView>,
    order: Vec<String>,
}

impl TaskStore {
    fn put(&mut self, task: TaskView) {
        if !self.tasks.contains_key(&task.id) {
            self.order.push(task.id.clone());
            if self.order.len() > MAX_TASKS {
                let oldest = self.order.remove(0);
                self.tasks.remove(&oldest);
            }
        }
        self.tasks.insert(task.id.clone(), task);
    }
}

struct SkillSchemas {
    input: String,
    output: String,
}

struct Executor {
    skills: HashMap<String, Arc<dyn Skill>>,
    schemas: HashMap<String, SkillSchemas>,
    planned: Vec<String>,
    settings: Arc<Settings>,
    contracts: Arc<Contracts>,
    events: Option<Arc<dyn EventSink>>,
    status: Arc<StatusTracker>,
    seen: Mutex<HashMap<String, SeenEntry>>,
    tasks: Arc<Mutex<TaskStore>>,
}

fn reject(task: TaskView, code: &str, text: impl Into<String>) -> TaskView {
    finish(
        task,
        &Outcome {
            output: None,
            error: Some(SkillError::new(code, text, false)),
            reject: true,
        },
    )
}

fn finish(mut task: TaskView, outcome: &Outcome) -> TaskView {
    match (&outcome.error, &outcome.output) {
        (Some(error), _) => {
            task.state = if outcome.reject {
                TaskState::Rejected
            } else {
                TaskState::Failed
            };
            task.message_text = Some(error.message.clone());
            task.error = Some(error.clone());
        }
        (None, output) => {
            task.state = TaskState::Completed;
            task.message_text = None;
            task.output = Some(Value::Object(output.clone().unwrap_or_default()));
        }
    }
    task
}

impl Executor {
    fn store(&self, task: &TaskView) {
        self.tasks.lock().unwrap().put(task.clone());
    }

    async fn execute(&self, caller: &str, inv: Invocation) -> TaskView {
        let task = TaskView {
            id: uuid::Uuid::new_v4().to_string(),
            context_id: inv
                .context_id
                .clone()
                .unwrap_or_else(|| uuid::Uuid::new_v4().to_string()),
            state: TaskState::Submitted,
            error: None,
            message_text: None,
            output: None,
        };
        let done = self.execute_inner(caller, inv, task.clone()).await;
        self.store(&done);
        done
    }

    async fn execute_inner(&self, caller: &str, inv: Invocation, task: TaskView) -> TaskView {
        let Some(skill_id) = inv
            .metadata
            .get(SKILL_KEY)
            .and_then(Value::as_str)
            .map(str::to_string)
        else {
            return reject(
                task,
                "INVALID_INPUT",
                format!("metadata {SKILL_KEY} missing"),
            );
        };
        let skill = self.skills.get(&skill_id);
        if skill.is_none() && self.planned.contains(&skill_id) {
            return reject(
                task,
                "NOT_IMPLEMENTED",
                format!("skill {skill_id} is planned, not implemented"),
            );
        }
        let (Some(skill), Some(schema)) = (skill, self.schemas.get(&skill_id)) else {
            return reject(task, "UNKNOWN_SKILL", format!("unknown skill {skill_id}"));
        };
        let [input] = inv.data_parts.as_slice() else {
            return reject(task, "INVALID_INPUT", "expected exactly one data part");
        };
        if let Err(e) = self.contracts.validate(&schema.input, input) {
            return reject(task, "INVALID_INPUT", e.to_string());
        }
        let Some(input) = input.as_object() else {
            return reject(task, "INVALID_INPUT", "data part must be an object");
        };
        let actor = match actor_from_metadata(caller, &inv.metadata) {
            Ok(a) => a,
            Err(e) => return reject(task, "FORBIDDEN", e.to_string()),
        };

        let key = format!("{caller}{SEP}{}", inv.message_id);
        let fingerprint = canonical_json(&json!([skill_id, input]));
        let done = {
            let mut seen = self.seen.lock().unwrap();
            if let Some(previous) = seen.get(&key) {
                if previous.fingerprint != fingerprint {
                    return reject(
                        task,
                        "INVALID_INPUT",
                        "messageId reused with a different request",
                    );
                }
                previous.done.clone()
            } else {
                let done = Arc::new(OnceCell::new());
                seen.insert(
                    key.clone(),
                    SeenEntry {
                        fingerprint,
                        done: done.clone(),
                    },
                );
                if seen.len() > MAX_SEEN {
                    seen.retain(|_, v| !v.done.initialized());
                }
                done
            }
        };

        let mut working = task.clone();
        working.state = TaskState::Working;
        self.store(&working);
        let last_progress = Mutex::new(None::<Instant>);
        let tasks = self.tasks.clone();
        let progress_task = working.clone();
        let ctx = SkillContext {
            task_id: task.id.clone(),
            message_id: inv.message_id.clone(),
            workflow_id: inv
                .metadata
                .get(WORKFLOW_KEY)
                .and_then(Value::as_str)
                .map(str::to_string),
            caller: caller.to_string(),
            actor,
            settings: self.settings.clone(),
            progress: Arc::new(move |text| {
                let mut last = last_progress.lock().unwrap();
                if last.is_some_and(|t| t.elapsed() < ACTIVITY_INTERVAL) {
                    return;
                }
                *last = Some(Instant::now());
                let mut t = progress_task.clone();
                t.message_text = Some(text.to_string());
                tasks.lock().unwrap().put(t);
            }),
        };
        let attributes = vec![
            opentelemetry::KeyValue::new("sandman.skill", skill_id.clone()),
            opentelemetry::KeyValue::new("sandman.task_id", task.id.clone()),
        ];
        let outcome = self
            .status
            .track(with_span::<Outcome, String>(
                "sandman.a2a.task",
                attributes,
                None,
                async {
                    Ok(done
                        .get_or_init(|| self.run(skill.as_ref(), &schema.output, &ctx, input))
                        .await
                        .clone())
                },
            ))
            .await
            .unwrap_or_else(|e| Outcome {
                output: None,
                error: Some(SkillError::new("INTERNAL", e, true)),
                reject: false,
            });
        if outcome.error.as_ref().is_some_and(|e| e.retryable) {
            let mut seen = self.seen.lock().unwrap();
            if seen.get(&key).is_some_and(|e| Arc::ptr_eq(&e.done, &done)) {
                seen.remove(&key);
            }
        }
        finish(task, &outcome)
    }

    async fn run(
        &self,
        skill: &dyn Skill,
        output_schema: &str,
        ctx: &SkillContext,
        input: &Map<String, Value>,
    ) -> Outcome {
        let fail = |error: SkillError| Outcome {
            output: None,
            error: Some(error),
            reject: false,
        };
        let result = match skill.run(ctx, input).await {
            Ok(r) => r,
            Err(SkillFailure::Skill(e)) => return fail(e),
            Err(SkillFailure::Budget(m)) => {
                return fail(SkillError::new("BUDGET_EXCEEDED", m, false));
            }
            Err(SkillFailure::Other(m)) => {
                eprintln!("skill failed: {m}");
                return fail(SkillError::new("INTERNAL", m, true));
            }
        };
        if let Err(e) = self
            .contracts
            .validate(output_schema, &Value::Object(result.output.clone()))
        {
            return fail(SkillError::new(
                "INTERNAL",
                format!("invalid output: {e}"),
                false,
            ));
        }
        if let Some((event_type, data)) = result.event {
            let Some(events) = &self.events else {
                return fail(SkillError::new(
                    "INTERNAL",
                    "completion event without event sink",
                    false,
                ));
            };
            let subject = input.get("projectId").and_then(Value::as_str).unwrap_or("");
            let options = PublishOptions {
                correlation_id: ctx.workflow_id.clone(),
                causation_id: Some(ctx.message_id.clone()),
                event_id: None,
            };
            if let Err(e) = events.publish(&event_type, subject, data, options).await {
                eprintln!("publishing completion event failed: {e}");
                return fail(SkillError::new(
                    "DEPENDENCY_UNAVAILABLE",
                    e.to_string(),
                    true,
                ));
            }
        }
        Outcome {
            output: Some(result.output),
            error: None,
            reject: false,
        }
    }
}

struct AppState {
    executor: Executor,
    authenticate: Authenticator,
    card: Value,
    status: Arc<StatusTracker>,
}

pub struct ServeOptions {
    pub agent_card_path: PathBuf,
    pub skills: Vec<Arc<dyn Skill>>,
    pub settings: Settings,
    pub events: Option<Arc<dyn EventSink>>,
    pub contracts: Option<Arc<Contracts>>,
    pub status: Option<Arc<StatusTracker>>,
}

fn unauthorized() -> Response {
    (
        StatusCode::UNAUTHORIZED,
        axum::Json(json!({"error": "missing or invalid token"})),
    )
        .into_response()
}

fn bearer(headers: &HeaderMap) -> Option<&str> {
    headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
}

fn url_path(url: &str) -> String {
    let rest = url.split_once("://").map_or(url, |(_, r)| r);
    rest.find('/').map_or("/".to_string(), |i| {
        rest[i..]
            .split(['?', '#'])
            .next()
            .unwrap_or("/")
            .to_string()
    })
}

async fn rpc(State(app): State<Arc<AppState>>, headers: HeaderMap, body: Bytes) -> Response {
    let Some(caller) = (app.authenticate)(bearer(&headers)) else {
        return unauthorized();
    };
    let body = match protocol::parse_request(&body) {
        Err(error) => error,
        Ok((id, protocol::Request::SendMessage(inv))) => {
            protocol::task_response(&id, &app.executor.execute(&caller, inv).await)
        }
        Ok((id, protocol::Request::GetTask(task_id))) => {
            let task = app
                .executor
                .tasks
                .lock()
                .unwrap()
                .tasks
                .get(&task_id)
                .cloned();
            match task {
                Some(t) => protocol::task_response(&id, &t),
                None => protocol::task_not_found(&id, &task_id),
            }
        }
        Ok((id, protocol::Request::Unsupported(method))) => protocol::unsupported(&id, &method),
    };
    axum::Json(body).into_response()
}

async fn agent_card(State(app): State<Arc<AppState>>) -> Response {
    axum::Json(app.card.clone()).into_response()
}

async fn status_route(State(app): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if (app.authenticate)(bearer(&headers)).is_none() {
        return unauthorized();
    }
    axum::Json(app.status.snapshot(None)).into_response()
}

pub fn serve(options: ServeOptions) -> Result<Router, String> {
    let path = options.agent_card_path.display().to_string();
    let card: Value = serde_json::from_str(
        &std::fs::read_to_string(&options.agent_card_path).map_err(|e| format!("{path}: {e}"))?,
    )
    .map_err(|e| format!("{path}: {e}"))?;
    protocol::validate_card(&card)?;
    let params = card["capabilities"]["extensions"]
        .as_array()
        .and_then(|exts| exts.iter().find(|e| e["uri"] == INVOCATION_EXTENSION))
        .map_or(Value::Null, |e| e["params"].clone());
    let schemas: HashMap<String, SkillSchemas> = params["skills"]
        .as_object()
        .map(|skills| {
            skills
                .iter()
                .map(|(id, s)| {
                    let name = |k: &str| s[k].as_str().unwrap_or("").to_string();
                    (
                        id.clone(),
                        SkillSchemas {
                            input: name("inputSchema"),
                            output: name("outputSchema"),
                        },
                    )
                })
                .collect()
        })
        .unwrap_or_default();
    let planned: Vec<String> = match &params["plannedSkills"] {
        Value::Array(a) => a
            .iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect(),
        Value::Object(o) => o.keys().cloned().collect(),
        _ => Vec::new(),
    };
    let missing: Vec<&str> = options
        .skills
        .iter()
        .map(|s| s.id())
        .filter(|id| !schemas.contains_key(*id))
        .collect();
    if !missing.is_empty() {
        return Err(format!(
            "skills without schemas in {path}: {}",
            missing.join(", ")
        ));
    }
    let rpc_path = card["supportedInterfaces"][0]["url"]
        .as_str()
        .map_or("/".to_string(), url_path);

    let settings = Arc::new(options.settings);
    let contracts = match options.contracts {
        Some(c) => c,
        None => Arc::new(Contracts::new(&settings.contracts_dir)?),
    };
    let status = options
        .status
        .unwrap_or_else(|| Arc::new(StatusTracker::new(&settings.service_name)));
    let authenticate = authenticator_for(
        settings.plugin_inbound_token.as_deref(),
        &settings.service_verify,
    );
    let executor = Executor {
        skills: options
            .skills
            .into_iter()
            .map(|s| (s.id().to_string(), s))
            .collect(),
        schemas,
        planned,
        settings,
        contracts,
        events: options.events,
        status: status.clone(),
        seen: Mutex::new(HashMap::new()),
        tasks: Arc::new(Mutex::new(TaskStore::default())),
    };
    let app = Arc::new(AppState {
        executor,
        authenticate,
        card,
        status,
    });
    Ok(Router::new()
        .route("/.well-known/agent-card.json", get(agent_card))
        .route(&rpc_path, post(rpc))
        .route("/v1/status", get(status_route))
        .with_state(app))
}
