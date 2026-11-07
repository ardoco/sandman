use crate::{store, AppState};
use serde::Serialize;
use serde_json::Value;
use std::time::Duration;
use tauri::State;

pub fn client(timeout_secs: u64) -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(timeout_secs))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .expect("http client")
}

pub fn token(state: &AppState, id: &str) -> Result<String, String> {
    if let Some(t) = state.tokens.lock().unwrap().get(id) {
        return Ok(t.clone());
    }
    let t = store::secret_get(&format!("token:{id}"))?.ok_or("no token saved for this project")?;
    state.tokens.lock().unwrap().insert(id.to_string(), t.clone());
    Ok(t)
}

pub fn valid_path(path: &str) -> bool {
    path.starts_with("/v1/")
        && path.chars().all(|c| c.is_ascii_alphanumeric() || "/_-.:".contains(c))
        && path.split('/').all(|s| s != "." && s != "..")
}

#[derive(Serialize)]
pub struct ApiResponse {
    status: u16,
    body: Value,
}

#[tauri::command]
pub async fn api(
    state: State<'_, AppState>,
    project_id: String,
    method: String,
    path: String,
    query: Option<Vec<(String, String)>>,
    body: Option<Value>,
) -> Result<ApiResponse, String> {
    if !valid_path(&path) {
        return Err("invalid API path".into());
    }
    let project = store::find(&state.dir, &project_id)?;
    let token = token(&state, &project_id)?;
    let url = format!("{}{}", project.url, path);
    let req = match method.as_str() {
        "GET" => client(120).get(url),
        "POST" => client(120).post(url),
        _ => return Err("method must be GET or POST".into()),
    };
    let mut req = req.bearer_auth(token).query(&query.unwrap_or_default());
    if let Some(b) = body {
        req = req.json(&b);
    }
    let resp = req.send().await.map_err(|e| format!("system not reachable: {}", e.without_url()))?;
    let status = resp.status().as_u16();
    let bytes = resp.bytes().await.map_err(|e| e.to_string())?;
    let body = serde_json::from_slice(&bytes).unwrap_or_else(|_| Value::String(String::from_utf8_lossy(&bytes).into_owned()));
    Ok(ApiResponse { status, body })
}

#[tauri::command]
pub fn set_token(state: State<'_, AppState>, project_id: String, token: String) -> Result<(), String> {
    let token = token.trim().to_string();
    if token.is_empty() || !token.chars().all(|c| c.is_ascii_graphic()) {
        return Err("token must be one printable word without spaces".into());
    }
    store::find(&state.dir, &project_id)?;
    store::secret_set(&format!("token:{project_id}"), &token)?;
    state.tokens.lock().unwrap().insert(project_id, token);
    Ok(())
}

#[tauri::command]
pub fn has_token(state: State<'_, AppState>, project_id: String) -> Result<bool, String> {
    Ok(token(&state, &project_id).is_ok())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_allow_list() {
        assert!(valid_path("/v1/projects/demo/workflows"));
        assert!(valid_path("/v1/inconsistencies/inc_ab12/transitions"));
        for bad in ["/readyz", "/v1/../admin", "/v1/a?x=1", "/v1/a#b", "/v1/a b", "/v1/%2e", "http://x/v1/a", "/v1/./a"] {
            assert!(!valid_path(bad), "{bad}");
        }
    }
}
