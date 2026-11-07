use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::telemetry::{trace_headers, with_span};

pub const LOCAL_ONLY: &str = "local-only";

#[derive(Debug)]
pub enum LlmError {
    Policy(String),
    BudgetExceeded(String),
    Gateway {
        status: Option<u16>,
        message: String,
    },
}

impl std::fmt::Display for LlmError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Policy(m) | Self::BudgetExceeded(m) => f.write_str(m),
            Self::Gateway { status, message } => write!(f, "gateway error {status:?}: {message}"),
        }
    }
}
impl std::error::Error for LlmError {}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Usage {
    pub input_tokens: u64,
    pub output_tokens: u64,
}

#[derive(Debug, Clone, Default)]
pub struct ChatRequest {
    pub model: String,
    pub messages: Vec<ChatMessage>,
    pub response_schema: Option<Value>,
    pub temperature: Option<f64>,
    pub seed: Option<i64>,
    pub max_tokens: Option<u32>,
    pub user: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ChatResponse {
    pub message: ChatMessage,
    pub finish_reason: String,
    pub usage: Usage,
    pub resolved_model: String,
}

const MAX_RETRIES: u32 = 2;

/// OpenAI-compatible gateway client (reqwest). 402 means the gateway budget is exhausted and blocks the workflow.
/// 429 and 5xx are retried twice.
pub struct LlmClient {
    http: reqwest::Client,
    base_url: String,
    api_key: String,
    routes: HashMap<String, String>,
    local_only: bool,
}

impl LlmClient {
    pub fn new(
        base_url: &str,
        api_key: &str,
        routes: HashMap<String, String>,
        local_only: bool,
    ) -> Self {
        Self {
            http: reqwest::Client::new(),
            base_url: base_url.trim_end_matches('/').to_string(),
            api_key: api_key.to_string(),
            routes,
            local_only,
        }
    }

    fn model(&self, route: &str) -> Result<&str, LlmError> {
        if self.local_only && !route.ends_with("-local") {
            return Err(LlmError::Policy(format!(
                "route '{route}' is not allowed for local-only projects"
            )));
        }
        self.routes
            .get(route)
            .map(String::as_str)
            .ok_or_else(|| LlmError::Policy(format!("unknown route '{route}'")))
    }

    async fn post(&self, path: &str, body: &Value) -> Result<Value, LlmError> {
        let gateway = |status, message: String| LlmError::Gateway { status, message };
        for attempt in 0..=MAX_RETRIES {
            let mut request = self
                .http
                .post(format!("{}{path}", self.base_url))
                .bearer_auth(&self.api_key)
                .json(body);
            for (k, v) in trace_headers() {
                request = request.header(k, v);
            }
            let outcome = request.send().await;
            let retry = attempt < MAX_RETRIES;
            let response = match outcome {
                Ok(r) => r,
                Err(e) if retry => {
                    tokio::time::sleep(std::time::Duration::from_millis(500 * 2u64.pow(attempt)))
                        .await;
                    let _ = e;
                    continue;
                }
                Err(e) => return Err(gateway(None, e.to_string())),
            };
            let status = response.status();
            if status.is_success() {
                return response
                    .json()
                    .await
                    .map_err(|e| gateway(Some(status.as_u16()), e.to_string()));
            }
            let text = response.text().await.unwrap_or_default();
            if status.as_u16() == 402 {
                return Err(LlmError::BudgetExceeded(text));
            }
            if retry && (status.as_u16() == 429 || status.is_server_error()) {
                tokio::time::sleep(std::time::Duration::from_millis(500 * 2u64.pow(attempt))).await;
                continue;
            }
            return Err(gateway(Some(status.as_u16()), text));
        }
        unreachable!("the loop returns on the last attempt")
    }

    pub async fn chat(&self, req: &ChatRequest) -> Result<ChatResponse, LlmError> {
        let model = self.model(&req.model)?.to_string();
        let mut body = json!({
            "model": model,
            "messages": req.messages,
            "temperature": req.temperature.unwrap_or(0.0),
        });
        if let Some(schema) = &req.response_schema {
            body["response_format"] = json!({"type": "json_schema", "json_schema": {"name": "response", "schema": schema, "strict": true}});
        }
        if let Some(v) = req.seed {
            body["seed"] = json!(v);
        }
        if let Some(v) = req.max_tokens {
            body["max_tokens"] = json!(v);
        }
        if let Some(v) = &req.user {
            body["user"] = json!(v);
        }
        let attributes = vec![opentelemetry::KeyValue::new("gen_ai.request.model", model)];
        with_span("sandman.llm.chat", attributes, None, async {
            let resp = self.post("/chat/completions", &body).await?;
            let choice = &resp["choices"][0];
            Ok(ChatResponse {
                message: ChatMessage {
                    role: "assistant".into(),
                    content: choice["message"]["content"]
                        .as_str()
                        .unwrap_or("")
                        .to_string(),
                },
                finish_reason: choice["finish_reason"].as_str().unwrap_or("").to_string(),
                usage: Usage {
                    input_tokens: resp["usage"]["prompt_tokens"].as_u64().unwrap_or(0),
                    output_tokens: resp["usage"]["completion_tokens"].as_u64().unwrap_or(0),
                },
                resolved_model: resp["model"].as_str().unwrap_or("").to_string(),
            })
        })
        .await
    }

    pub async fn embed(&self, route: &str, texts: &[&str]) -> Result<Vec<Vec<f64>>, LlmError> {
        let model = self.model(route)?;
        let resp = self
            .post("/embeddings", &json!({"model": model, "input": texts}))
            .await?;
        Ok(resp["data"]
            .as_array()
            .map(|items| {
                items
                    .iter()
                    .map(|d| {
                        d["embedding"]
                            .as_array()
                            .map(|v| v.iter().filter_map(Value::as_f64).collect())
                            .unwrap_or_default()
                    })
                    .collect()
            })
            .unwrap_or_default())
    }
}
