use crate::{errcode::coded, store, AppState};
use serde::Serialize;
use serde_json::{json, Value};
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
    let t = store::secret_get(&format!("token:{id}"))?.ok_or_else(|| coded("token_missing", json!({}), "no token saved for this project"))?;
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
    let resp = req.send().await.map_err(|e| not_reachable(e))?;
    let status = resp.status().as_u16();
    let bytes = resp.bytes().await.map_err(|e| e.to_string())?;
    let body = serde_json::from_slice(&bytes).unwrap_or_else(|_| Value::String(String::from_utf8_lossy(&bytes).into_owned()));
    Ok(ApiResponse { status, body })
}

#[tauri::command]
pub fn set_token(state: State<'_, AppState>, project_id: String, token: String) -> Result<(), String> {
    let token = token.trim().to_string();
    if token.is_empty() || !token.chars().all(|c| c.is_ascii_graphic()) {
        return Err(coded("token_format", json!({}), "token must be one printable word without spaces"));
    }
    store::find(&state.dir, &project_id)?;
    store::secret_set(&format!("token:{project_id}"), &token)?;
    state.tokens.lock().unwrap().insert(project_id, token);
    Ok(())
}

pub fn not_reachable(e: reqwest::Error) -> String {
    let detail = e.without_url().to_string();
    coded("not_reachable", json!({"detail": detail}), format!("system not reachable: {detail}"))
}

pub fn read_secret_file(checkout: &str, name: &str) -> Result<String, String> {
    let path = std::path::Path::new(checkout).join("deploy/secrets").join(name);
    let text = std::fs::read_to_string(&path).map_err(|_| coded("secret_file_missing", json!({"path": path.display().to_string()}), format!("{} not found. Start the stack once to create it.", path.display())))?;
    let value = text.trim().to_string();
    if value.is_empty() {
        return Err(coded("secret_file_empty", json!({"path": path.display().to_string()}), format!("{} is empty", path.display())));
    }
    Ok(value)
}

#[tauri::command]
pub fn admin_password(state: State<'_, AppState>, project_id: String) -> Result<String, String> {
    let project = store::find(&state.dir, &project_id)?;
    let checkout = project.checkout.filter(|_| project.mode == "local").ok_or_else(|| coded("admin_local_only", json!({}), "only a local project has an admin password file"))?;
    read_secret_file(&checkout, "admin_bootstrap_password")
}

#[tauri::command]
pub async fn check_token(state: State<'_, AppState>, project_id: String, token: String) -> Result<ApiResponse, String> {
    let token = token.trim().to_string();
    if token.is_empty() || !token.chars().all(|c| c.is_ascii_graphic()) {
        return Err(coded("token_format", json!({}), "token must be one printable word without spaces"));
    }
    let project = store::find(&state.dir, &project_id)?;
    let resp = client(10)
        .get(format!("{}/v1/auth/me", project.url))
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| not_reachable(e))?;
    let status = resp.status().as_u16();
    let body = resp.json::<Value>().await.unwrap_or(Value::Null);
    if status == 200 {
        store::secret_set(&format!("token:{project_id}"), &token)?;
        state.tokens.lock().unwrap().insert(project_id, token);
    }
    Ok(ApiResponse { status, body })
}

#[derive(Serialize, Debug, Default, PartialEq)]
pub struct Skipped {
    id: String,
    reason: String,
}

#[derive(Serialize, Debug, Default, PartialEq)]
pub struct Plan {
    added: Vec<String>,
    updated: Vec<String>,
    skipped: Vec<Skipped>,
    #[serde(skip)]
    upserts: Vec<store::Project>,
}

pub fn plan_connect(existing: &[store::Project], listed: &[(String, String)], url: &str) -> Plan {
    let mut plan = Plan::default();
    for (id, name) in listed {
        let found = existing.iter().find(|p| &p.id == id);
        let mut project = match found {
            Some(p) if p.mode != "remote" => {
                plan.skipped.push(Skipped { id: id.clone(), reason: "a local project with this id exists".into() });
                continue;
            }
            Some(p) if p.url != url => {
                plan.skipped.push(Skipped { id: id.clone(), reason: format!("a project with this id exists for {}", p.url) });
                continue;
            }
            Some(p) => store::Project { name: name.clone(), ..p.clone() },
            None => store::Project { id: id.clone(), name: name.clone(), mode: "remote".into(), url: url.into(), checkout: None, cli_command: None, last_used: 0 },
        };
        if let Err(reason) = store::validate(&mut project) {
            plan.skipped.push(Skipped { id: id.clone(), reason });
            continue;
        }
        if found.is_some() { plan.updated.push(id.clone()) } else { plan.added.push(id.clone()) }
        plan.upserts.push(project);
    }
    plan
}

#[derive(Serialize)]
pub struct Connected {
    status: u16,
    body: Value,
    username: String,
    #[serde(flatten)]
    plan: Plan,
    warning: Option<String>,
}

#[tauri::command]
pub async fn connect_system(state: State<'_, AppState>, url: String, token: String) -> Result<Connected, String> {
    let token = token.trim().to_string();
    if token.is_empty() || !token.chars().all(|c| c.is_ascii_graphic()) {
        return Err(coded("token_format", json!({}), "token must be one printable word without spaces"));
    }
    let (url, warning) = store::valid_url(&url)?;
    finish_connect(&state, url, token, warning).await
}

async fn finish_connect(state: &AppState, url: String, token: String, warning: Option<String>) -> Result<Connected, String> {
    let get = |path: &str| {
        client(20).get(format!("{url}{path}")).bearer_auth(&token).send()
    };
    let unreachable = not_reachable;
    let me = get("/v1/auth/me").await.map_err(unreachable)?;
    let status = me.status().as_u16();
    let body = me.json::<Value>().await.unwrap_or(Value::Null);
    if status != 200 {
        return Ok(Connected { status, body, username: String::new(), plan: Plan::default(), warning });
    }
    let username = body["username"].as_str().unwrap_or_default().to_string();
    let resp = get("/v1/projects").await.map_err(unreachable)?;
    let status = resp.status().as_u16();
    let body = resp.json::<Value>().await.unwrap_or(Value::Null);
    if status != 200 {
        return Ok(Connected { status, body, username, plan: Plan::default(), warning });
    }
    let listed: Vec<(String, String)> = body
        .as_array()
        .ok_or("unexpected project list")?
        .iter()
        .filter_map(|p| Some((p["id"].as_str()?.to_string(), p["name"].as_str()?.to_string())))
        .collect();
    let mut plan = plan_connect(&store::load_projects(&state.dir)?, &listed, &url);
    for p in std::mem::take(&mut plan.upserts) {
        store::secret_set(&format!("token:{}", p.id), &token)?;
        state.tokens.lock().unwrap().insert(p.id.clone(), token.clone());
        store::upsert(&state.dir, p)?;
    }
    Ok(Connected { status: 200, body: Value::Null, username, plan, warning })
}

pub fn login_body(url: &str, username: &str, password: &str) -> Value {
    let host = url::Url::parse(url).ok().and_then(|u| u.host_str().map(str::to_string)).unwrap_or_default();
    let name: String = format!("desktop {host}").chars().take(100).collect();
    serde_json::json!({ "username": username, "password": password, "name": name })
}

#[tauri::command]
pub async fn login_connect(state: State<'_, AppState>, url: String, username: String, password: String) -> Result<Connected, String> {
    let username = username.trim().to_string();
    if username.is_empty() || password.is_empty() {
        return Err(coded("credentials_required", json!({}), "username and password are required"));
    }
    let (url, warning) = store::valid_url(&url)?;
    if warning.is_some() {
        return Err(coded("password_insecure", json!({}), "The password is not sent without encryption. Use https for a remote system."));
    }
    let resp = client(20)
        .post(format!("{url}/v1/auth/login-token"))
        .json(&login_body(&url, &username, &password))
        .send()
        .await
        .map_err(|e| not_reachable(e))?;
    let status = resp.status().as_u16();
    let body = resp.json::<Value>().await.unwrap_or(Value::Null);
    let secret = body["secret"].as_str().map(str::to_string);
    match (status, secret) {
        (201, Some(token)) => finish_connect(&state, url, token, warning).await,
        (201, None) => Err(coded("login_unexpected", json!({}), "unexpected login response")),
        _ => Ok(Connected { status, body, username: String::new(), plan: Plan::default(), warning }),
    }
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

    fn remote(id: &str, url: &str, used: u64) -> store::Project {
        store::Project { id: id.into(), name: "old".into(), mode: "remote".into(), url: url.into(), checkout: None, cli_command: None, last_used: used }
    }

    fn listed(ids: &[&str]) -> Vec<(String, String)> {
        ids.iter().map(|i| (i.to_string(), format!("Name {i}"))).collect()
    }

    #[test]
    fn connect_empty_list_changes_nothing() {
        assert_eq!(plan_connect(&[remote("aa", "https://s.example", 3)], &[], "https://s.example"), Plan::default());
    }

    #[test]
    fn connect_restricted_token_adds_one_project() {
        let plan = plan_connect(&[], &listed(&["bb"]), "https://s.example");
        assert_eq!(plan.added, vec!["bb"]);
        assert_eq!(plan.upserts[0].name, "Name bb");
        assert_eq!(plan.upserts[0].mode, "remote");
        assert_eq!(plan.upserts[0].last_used, 0);
    }

    #[test]
    fn connect_same_url_updates_name_and_keeps_last_used() {
        let plan = plan_connect(&[remote("aa", "https://s.example", 7)], &listed(&["aa"]), "https://s.example");
        assert_eq!(plan.updated, vec!["aa"]);
        assert!(plan.added.is_empty());
        assert_eq!((plan.upserts[0].name.as_str(), plan.upserts[0].last_used), ("Name aa", 7));
    }

    #[test]
    fn connect_never_overwrites_local_or_other_system() {
        let mut local = remote("aa", "http://127.0.0.1:8080", 1);
        local.mode = "local".into();
        let other = remote("bb", "https://other.example", 1);
        let plan = plan_connect(&[local, other], &listed(&["aa", "bb", "cc"]), "https://s.example");
        assert_eq!(plan.added, vec!["cc"]);
        assert_eq!(plan.skipped.iter().map(|s| s.id.as_str()).collect::<Vec<_>>(), vec!["aa", "bb"]);
        assert!(plan.skipped[0].reason.contains("local"));
        assert!(plan.skipped[1].reason.contains("https://other.example"));
        assert_eq!(plan.upserts.len(), 1);
    }

    #[test]
    fn connect_skips_invalid_id() {
        let plan = plan_connect(&[], &listed(&["A_b"]), "https://s.example");
        assert_eq!(plan.skipped.len(), 1);
        assert!(plan.added.is_empty());
    }

    #[test]
    fn login_body_names_the_token_after_the_host() {
        let body = login_body("https://sandman.example:8443", "alice", "pw");
        assert_eq!(body["name"], "desktop sandman.example");
        assert_eq!(body["username"], "alice");
        assert_eq!(body["password"], "pw");
        assert_eq!(body.as_object().unwrap().len(), 3);
    }

    #[test]
    fn login_body_name_is_at_most_100_chars() {
        let url = format!("https://{}.example", "a".repeat(120));
        assert_eq!(login_body(&url, "u", "p")["name"].as_str().unwrap().chars().count(), 100);
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
