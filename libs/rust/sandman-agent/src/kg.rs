use std::time::Duration;

use async_trait::async_trait;
use serde_json::{Map, Value, json};

use crate::config::{Env, llm_mode, process_env};
use crate::ids::new_run_id;
use crate::llm::Usage;
use crate::status::now_rfc3339;
use crate::telemetry::with_span;

pub const MEMORY_LIMIT_CODE: &str = "Neo.TransientError.General.MemoryPoolOutOfMemoryError";

pub type Row = Map<String, Value>;
pub type Params = Map<String, Value>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum KgError {
    /// Traverse query memory limit. Never retried.
    MemoryLimit(String),
    /// Transient server error. `KgClient` retries it with backoff.
    Transient(String),
    Other(String),
}

impl std::fmt::Display for KgError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::MemoryLimit(m) | Self::Transient(m) | Self::Other(m) => f.write_str(m),
        }
    }
}
impl std::error::Error for KgError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Read,
    Write,
}

pub type Statement = (String, Params);

/// One transaction (no driver-side retry) or one autocommit statement. The real implementation is `Neo4rsBackend`.
#[async_trait]
pub trait KgBackend: Send + Sync {
    async fn transaction(
        &self,
        mode: Mode,
        statements: &[Statement],
    ) -> Result<Vec<Vec<Row>>, KgError>;
    async fn autocommit(&self, cypher: &str, params: &Params) -> Result<Vec<Row>, KgError>;
}

pub fn classify_code(code: &str, message: &str) -> KgError {
    if code == MEMORY_LIMIT_CODE {
        KgError::MemoryLimit(message.to_string())
    } else if code.starts_with("Neo.TransientError.") {
        KgError::Transient(message.to_string())
    } else {
        KgError::Other(message.to_string())
    }
}

fn map_neo4rs(e: neo4rs::Error) -> KgError {
    match &e {
        neo4rs::Error::Neo4j(n) => classify_code(n.code(), n.message()),
        neo4rs::Error::ConnectionError
        | neo4rs::Error::ConnectionTimedOut
        | neo4rs::Error::ServerUnavailableError(_)
        | neo4rs::Error::IOError { .. } => KgError::Transient(e.to_string()),
        _ => KgError::Other(e.to_string()),
    }
}

fn to_query(cypher: &str, params: &Params) -> Result<neo4rs::Query, KgError> {
    let mut query = neo4rs::query(cypher);
    for (key, value) in params {
        let bolt = neo4rs::BoltType::try_from(value.clone())
            .map_err(|e| KgError::Other(format!("parameter {key}: {e}")))?;
        query = query.param(key, bolt);
    }
    Ok(query)
}

fn to_row(row: &neo4rs::Row) -> Result<Row, KgError> {
    match row.to::<Value>() {
        Ok(Value::Object(map)) => Ok(map),
        Ok(other) => Err(KgError::Other(format!("unexpected row shape: {other}"))),
        Err(e) => Err(KgError::Other(e.to_string())),
    }
}

pub struct Neo4rsBackend {
    graph: neo4rs::Graph,
}

impl Neo4rsBackend {
    pub fn connect(uri: &str, user: Option<&str>, password: Option<&str>) -> Result<Self, KgError> {
        let config = neo4rs::ConfigBuilder::default()
            .uri(uri)
            .user(user.unwrap_or(""))
            .password(password.unwrap_or(""))
            .build()
            .map_err(map_neo4rs)?;
        Ok(Self {
            graph: neo4rs::Graph::connect(config).map_err(map_neo4rs)?,
        })
    }
}

#[async_trait]
impl KgBackend for Neo4rsBackend {
    async fn transaction(
        &self,
        mode: Mode,
        statements: &[Statement],
    ) -> Result<Vec<Vec<Row>>, KgError> {
        let operation = if mode == Mode::Read {
            neo4rs::Operation::Read
        } else {
            neo4rs::Operation::Write
        };
        let mut txn = self
            .graph
            .start_txn_as(operation, None)
            .await
            .map_err(map_neo4rs)?;
        let mut results = Vec::new();
        for (cypher, params) in statements {
            let outcome: Result<Vec<Row>, KgError> = async {
                let mut stream = txn
                    .execute(to_query(cypher, params)?)
                    .await
                    .map_err(map_neo4rs)?;
                let mut rows = Vec::new();
                while let Some(row) = stream.next(txn.handle()).await.map_err(map_neo4rs)? {
                    rows.push(to_row(&row)?);
                }
                Ok(rows)
            }
            .await;
            match outcome {
                Ok(rows) => results.push(rows),
                Err(e) => {
                    let _ = txn.rollback().await;
                    return Err(e);
                }
            }
        }
        txn.commit().await.map_err(map_neo4rs)?;
        Ok(results)
    }

    async fn autocommit(&self, cypher: &str, params: &Params) -> Result<Vec<Row>, KgError> {
        let mut stream = self
            .graph
            .execute(to_query(cypher, params)?)
            .await
            .map_err(map_neo4rs)?;
        let mut rows = Vec::new();
        while let Some(row) = stream.next().await.map_err(map_neo4rs)? {
            rows.push(to_row(&row)?);
        }
        Ok(rows)
    }
}

#[async_trait]
pub trait KgWrite: Send + Sync {
    async fn write(&self, cypher: &str, params: Params) -> Result<Vec<Row>, KgError>;
}

pub struct KgClient<B: KgBackend> {
    backend: B,
    backoff: Vec<Duration>,
}

impl KgClient<Neo4rsBackend> {
    pub fn connect(uri: &str, user: Option<&str>, password: Option<&str>) -> Result<Self, KgError> {
        Ok(Self::new(Neo4rsBackend::connect(uri, user, password)?))
    }
}

impl<B: KgBackend> KgClient<B> {
    pub fn new(backend: B) -> Self {
        Self {
            backend,
            backoff: vec![
                Duration::from_millis(200),
                Duration::from_millis(800),
                Duration::from_secs(2),
            ],
        }
    }

    pub fn with_backoff(mut self, backoff: Vec<Duration>) -> Self {
        self.backoff = backoff;
        self
    }

    async fn retrying(
        &self,
        op: &'static str,
        mode: Mode,
        statements: &[Statement],
    ) -> Result<Vec<Vec<Row>>, KgError> {
        let statement = statements
            .first()
            .map_or("", |s| s.0.as_str())
            .chars()
            .take(200)
            .collect::<String>();
        let attributes = vec![opentelemetry::KeyValue::new("db.statement", statement)];
        with_span(op, attributes, None, async {
            for attempt in 0.. {
                match self.backend.transaction(mode, statements).await {
                    Err(KgError::Transient(_)) if attempt < self.backoff.len() => {
                        tokio::time::sleep(self.backoff[attempt]).await;
                    }
                    other => return other,
                }
            }
            unreachable!()
        })
        .await
    }

    /// Runs in a read transaction. Traverse rejects the words drop, set, delete, create, merge and remove in read
    /// query text, also in string literals: pass them as parameters. A query whose rows enter an LLM prompt ends in
    /// ORDER BY on a stable key.
    pub async fn read(&self, cypher: &str, params: Params) -> Result<Vec<Row>, KgError> {
        let mut out = self
            .retrying("sandman.kg.read", Mode::Read, &[(cypher.into(), params)])
            .await?;
        Ok(out.remove(0))
    }

    pub async fn write(&self, cypher: &str, params: Params) -> Result<Vec<Row>, KgError> {
        let mut out = self
            .retrying("sandman.kg.write", Mode::Write, &[(cypher.into(), params)])
            .await?;
        Ok(out.remove(0))
    }

    /// Several statements in one write transaction (divergence from the TypeScript `writeTx` callback).
    pub async fn write_tx(&self, statements: &[Statement]) -> Result<Vec<Vec<Row>>, KgError> {
        self.retrying("sandman.kg.write_tx", Mode::Write, statements)
            .await
    }

    pub async fn run_autocommit(&self, cypher: &str, params: Params) -> Result<Vec<Row>, KgError> {
        let attributes = vec![opentelemetry::KeyValue::new(
            "db.statement",
            cypher.chars().take(200).collect::<String>(),
        )];
        with_span(
            "sandman.kg.autocommit",
            attributes,
            None,
            self.backend.autocommit(cypher, &params),
        )
        .await
    }

    /// CREATE INDEX has no IF NOT EXISTS in Traverse: an "already exists" error is ignored.
    pub async fn init_schema(&self, cypher_file: &std::path::Path) -> Result<(), KgError> {
        let text =
            std::fs::read_to_string(cypher_file).map_err(|e| KgError::Other(e.to_string()))?;
        let code: String = text
            .lines()
            .filter(|l| !l.trim_start().starts_with("//"))
            .collect::<Vec<_>>()
            .join("\n");
        for statement in code.split(';').map(str::trim).filter(|s| !s.is_empty()) {
            if let Err(e) = self.run_autocommit(statement, Params::new()).await
                && !e.to_string().contains("already exists")
            {
                return Err(e);
            }
        }
        Ok(())
    }
}

#[async_trait]
impl<B: KgBackend> KgWrite for KgClient<B> {
    async fn write(&self, cypher: &str, params: Params) -> Result<Vec<Row>, KgError> {
        KgClient::write(self, cypher, params).await
    }
}

#[derive(Debug, Clone, Default)]
pub struct RunStart {
    pub project_id: String,
    pub skill: String,
    pub tool: String,
    pub tool_version: String,
    pub input_key: String,
    pub model: Option<String>,
    pub params: Option<String>,
    pub config_hash: Option<String>,
    pub used_version_ids: Vec<String>,
    pub workflow_id: Option<String>,
    pub task_id: Option<String>,
    pub triggered_by: Option<String>,
    pub replay_mode: Option<String>,
    pub prompt_ids: Vec<String>,
}

pub struct RunRecorder<K: KgWrite> {
    kg: K,
    agent: String,
    agent_version: String,
    env: Env,
}

fn params(value: Value) -> Params {
    match value {
        Value::Object(m) => m,
        _ => Params::new(),
    }
}

impl<K: KgWrite> RunRecorder<K> {
    pub fn new(kg: K, agent: &str, agent_version: &str) -> Self {
        Self {
            kg,
            agent: agent.into(),
            agent_version: agent_version.into(),
            env: process_env(),
        }
    }

    pub fn with_env(mut self, env: Env) -> Self {
        self.env = env;
        self
    }

    pub async fn start(&self, r: RunStart) -> Result<String, KgError> {
        let id = new_run_id();
        let replay_mode = match r.replay_mode {
            Some(m) => m,
            None => llm_mode(&self.env).map_err(|e| KgError::Other(e.to_string()))?,
        };
        let mut props = json!({
            "id": id, "projectId": r.project_id, "agent": self.agent, "agentVersion": self.agent_version,
            "skill": r.skill, "tool": r.tool, "toolVersion": r.tool_version, "inputKey": r.input_key,
            "replayMode": replay_mode, "status": "running", "startedAt": now_rfc3339(),
        });
        for (key, value) in [
            ("model", &r.model),
            ("params", &r.params),
            ("configHash", &r.config_hash),
            ("workflowId", &r.workflow_id),
            ("a2aTaskId", &r.task_id),
            ("triggeredBy", &r.triggered_by),
        ] {
            if let Some(v) = value {
                props[key] = json!(v);
            }
        }
        if !r.prompt_ids.is_empty() {
            props["promptIds"] = json!(r.prompt_ids);
        }
        self.kg
            .write(
                "CREATE (r:Run) SET r = $props \
                 WITH r UNWIND $versions AS vid MATCH (v:ArtifactVersion {id: vid}) CREATE (r)-[:USED]->(v)",
                params(json!({"props": props, "versions": r.used_version_ids})),
            )
            .await?;
        Ok(id)
    }

    pub async fn complete(
        &self,
        run_id: &str,
        usage: Option<Usage>,
        status: &str,
        resolved_model: Option<&str>,
        mixed_models: bool,
    ) -> Result<(), KgError> {
        self.kg
            .write(
                "MATCH (r:Run {id: $id}) SET r.status = $status, r.endedAt = $now, \
                 r.inputTokens = $inp, r.outputTokens = $out, r.resolvedModel = $resolved, r.mixedModels = $mixed",
                params(json!({
                    "id": run_id, "status": status, "now": now_rfc3339(), "resolved": resolved_model,
                    "mixed": if mixed_models { json!(true) } else { Value::Null },
                    "inp": usage.map(|u| u.input_tokens), "out": usage.map(|u| u.output_tokens),
                })),
            )
            .await?;
        Ok(())
    }

    pub async fn fail(&self, run_id: &str, error: &str) -> Result<(), KgError> {
        self.kg
            .write(
                "MATCH (r:Run {id: $id}) SET r.status = 'failed', r.error = $error, r.endedAt = $now",
                params(json!({"id": run_id, "error": error, "now": now_rfc3339()})),
            )
            .await?;
        Ok(())
    }

    pub async fn cleanup_stale(&self, input_key: &str) -> Result<u64, KgError> {
        let rows = self
            .kg
            .write(
                "MATCH (r:Run {inputKey: $key}) WHERE r.status IN ['running', 'failed'] \
                 OPTIONAL MATCH (r)-[:PRODUCED|DETECTED]->(f) WHERE f.runId = r.id \
                 WITH r, collect(f) AS facts \
                 SET r.status = 'failed', r.endedAt = coalesce(r.endedAt, $now) \
                 WITH facts UNWIND facts AS f DETACH DELETE f RETURN count(f) AS deleted",
                params(json!({"key": input_key, "now": now_rfc3339()})),
            )
            .await?;
        Ok(rows
            .first()
            .and_then(|r| r.get("deleted"))
            .and_then(Value::as_u64)
            .unwrap_or(0))
    }
}
