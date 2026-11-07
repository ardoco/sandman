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

pub fn read_secret_file(checkout: &str, name: &str) -> Result<String, String> {
    let path = std::path::Path::new(checkout).join("deploy/secrets").join(name);
    let text = std::fs::read_to_string(&path).map_err(|_| format!("{} not found. Start the stack once to create it.", path.display()))?;
    let value = text.trim().to_string();
    if value.is_empty() {
        return Err(format!("{} is empty", path.display()));
    }
    Ok(value)
}

#[tauri::command]
pub fn admin_password(state: State<'_, AppState>, project_id: String) -> Result<String, String> {
    let project = store::find(&state.dir, &project_id)?;
    let checkout = project.checkout.filter(|_| project.mode == "local").ok_or("only a local project has an admin password file")?;
    read_secret_file(&checkout, "admin_bootstrap_password")
}

#[tauri::command]
pub async fn check_token(state: State<'_, AppState>, project_id: String, token: String) -> Result<ApiResponse, String> {
    let token = token.trim().to_string();
    if token.is_empty() || !token.chars().all(|c| c.is_ascii_graphic()) {
        return Err("token must be one printable word without spaces".into());
    }
    let project = store::find(&state.dir, &project_id)?;
    let resp = client(10)
        .get(format!("{}/v1/auth/me", project.url))
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("system not reachable: {}", e.without_url()))?;
    let status = resp.status().as_u16();
    let body = resp.json::<Value>().await.unwrap_or(Value::Null);
    if status == 200 {
        store::secret_set(&format!("token:{project_id}"), &token)?;
        state.tokens.lock().unwrap().insert(project_id, token);
    }
    Ok(ApiResponse { status, body })
}

#[tauri::command]
pub fn has_token(state: State<'_, AppState>, project_id: String) -> Result<bool, String> {
    Ok(token(&state, &project_id).is_ok())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_secret_file_trimmed_and_reports_missing() {
        let d = tempfile::tempdir().unwrap();
        let co = d.path().to_string_lossy().into_owned();
        assert!(read_secret_file(&co, "admin_bootstrap_password").unwrap_err().contains("not found"));
        std::fs::create_dir_all(d.path().join("deploy/secrets")).unwrap();
        std::fs::write(d.path().join("deploy/secrets/admin_bootstrap_password"), "s3cret\n").unwrap();
        assert_eq!(read_secret_file(&co, "admin_bootstrap_password").unwrap(), "s3cret");
        std::fs::write(d.path().join("deploy/secrets/admin_bootstrap_password"), "\n").unwrap();
        assert!(read_secret_file(&co, "admin_bootstrap_password").unwrap_err().contains("empty"));
    }

    #[test]
    fn path_allow_list() {
        assert!(valid_path("/v1/projects/demo/workflows"));
        assert!(valid_path("/v1/inconsistencies/inc_ab12/transitions"));
        for bad in ["/readyz", "/v1/../admin", "/v1/a?x=1", "/v1/a#b", "/v1/a b", "/v1/%2e", "http://x/v1/a", "/v1/./a"] {
            assert!(!valid_path(bad), "{bad}");
        }
    }
}
