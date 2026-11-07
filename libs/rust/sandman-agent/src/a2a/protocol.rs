//! The only module that uses `a2a-lf` (RK-23). A 1.0 upgrade of the crate stays local to this file.
//! The rest of the kit sees plain `serde_json` values and the types defined in `super`.
use serde_json::{Map, Value, json};

use super::{ERROR_KEY, TaskState, TaskView};

#[derive(Debug, Clone)]
pub struct Invocation {
    pub message_id: String,
    pub context_id: Option<String>,
    pub metadata: Map<String, Value>,
    pub data_parts: Vec<Value>,
}

#[derive(Debug)]
pub enum Request {
    SendMessage(Invocation),
    GetTask(String),
    Unsupported(String),
}

pub type RpcId = Value;

fn error_body(id: &RpcId, error: a2a_lf::A2AError) -> Value {
    let id: a2a_lf::JsonRpcId =
        serde_json::from_value(id.clone()).unwrap_or(a2a_lf::JsonRpcId::Null);
    serde_json::to_value(a2a_lf::JsonRpcResponse::error(id, error.to_jsonrpc_error()))
        .unwrap_or(Value::Null)
}

pub fn parse_request(body: &[u8]) -> Result<(RpcId, Request), Value> {
    let request: a2a_lf::JsonRpcRequest = serde_json::from_slice(body)
        .map_err(|e| error_body(&Value::Null, a2a_lf::A2AError::parse_error(e.to_string())))?;
    let id = serde_json::to_value(&request.id).unwrap_or(Value::Null);
    if request.jsonrpc != "2.0" {
        return Err(error_body(
            &id,
            a2a_lf::A2AError::invalid_request("jsonrpc must be 2.0"),
        ));
    }
    let params = request.params.unwrap_or(Value::Null);
    let invalid =
        |e: serde_json::Error| error_body(&id, a2a_lf::A2AError::invalid_params(e.to_string()));
    let parsed = match request.method.as_str() {
        a2a_lf::methods::SEND_MESSAGE => {
            let req: a2a_lf::SendMessageRequest =
                serde_json::from_value(params).map_err(invalid)?;
            let data_parts = req
                .message
                .parts
                .iter()
                .filter_map(|p| match &p.content {
                    a2a_lf::PartContent::Data(v) => Some(v.clone()),
                    _ => None,
                })
                .collect();
            Request::SendMessage(Invocation {
                message_id: req.message.message_id,
                context_id: req.message.context_id,
                metadata: req
                    .message
                    .metadata
                    .unwrap_or_default()
                    .into_iter()
                    .collect(),
                data_parts,
            })
        }
        a2a_lf::methods::GET_TASK => {
            let req: a2a_lf::GetTaskRequest = serde_json::from_value(params).map_err(invalid)?;
            Request::GetTask(req.id)
        }
        m if a2a_lf::methods::is_valid(m) => Request::Unsupported(m.to_string()),
        m => return Err(error_body(&id, a2a_lf::A2AError::method_not_found(m))),
    };
    Ok((id, parsed))
}

fn state_name(state: TaskState) -> &'static str {
    match state {
        TaskState::Submitted => "TASK_STATE_SUBMITTED",
        TaskState::Working => "TASK_STATE_WORKING",
        TaskState::Completed => "TASK_STATE_COMPLETED",
        TaskState::Failed => "TASK_STATE_FAILED",
        TaskState::Rejected => "TASK_STATE_REJECTED",
    }
}

fn task_value(task: &TaskView) -> Result<Value, String> {
    let message = task.message_text.as_ref().map(|text| {
        let mut message = json!({
            "messageId": uuid::Uuid::new_v4().to_string(),
            "contextId": task.context_id,
            "taskId": task.id,
            "role": "ROLE_AGENT",
            "parts": [{"text": text, "mediaType": "text/plain"}],
        });
        if let Some(error) = &task.error {
            message["metadata"] = json!({ ERROR_KEY: error.to_json() });
        }
        message
    });
    let mut status =
        json!({"state": state_name(task.state), "timestamp": crate::status::now_rfc3339()});
    if let Some(message) = message {
        status["message"] = message;
    }
    let mut value = json!({"id": task.id, "contextId": task.context_id, "status": status});
    if let Some(output) = &task.output {
        value["artifacts"] = json!([{
            "artifactId": uuid::Uuid::new_v4().to_string(),
            "name": "result",
            "parts": [{"data": output, "mediaType": "application/json"}],
        }]);
    }
    let typed: a2a_lf::Task = serde_json::from_value(value).map_err(|e| e.to_string())?;
    serde_json::to_value(typed).map_err(|e| e.to_string())
}

pub fn task_response(id: &RpcId, task: &TaskView) -> Value {
    let rpc_id: a2a_lf::JsonRpcId =
        serde_json::from_value(id.clone()).unwrap_or(a2a_lf::JsonRpcId::Null);
    match task_value(task) {
        Ok(t) => serde_json::to_value(a2a_lf::JsonRpcResponse::success(rpc_id, json!({"task": t})))
            .unwrap_or(Value::Null),
        Err(e) => error_body(id, a2a_lf::A2AError::internal(e)),
    }
}

pub fn task_not_found(id: &RpcId, task_id: &str) -> Value {
    error_body(id, a2a_lf::A2AError::task_not_found(task_id))
}

pub fn unsupported(id: &RpcId, method: &str) -> Value {
    error_body(
        id,
        a2a_lf::A2AError::unsupported_operation(format!("{method} is not supported")),
    )
}

/// Parses the card with the `a2a-lf` model to reject malformed cards at start-up. The card is served unchanged.
pub fn validate_card(raw: &Value) -> Result<(), String> {
    serde_json::from_value::<a2a_lf::AgentCard>(raw.clone())
        .map(|_| ())
        .map_err(|e| format!("invalid agent card: {e}"))
}
