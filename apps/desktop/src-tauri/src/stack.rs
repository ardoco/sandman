use crate::{store, AppState};
use serde::Serialize;
use std::process::Stdio;
use tauri::{AppHandle, Emitter, State};
use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};
use tokio::process::Command;

pub fn command(program: &str) -> Command {
    let mut c = Command::new(program);
    if cfg!(unix) {
        let home = std::env::var("HOME").unwrap_or_default();
        let path = std::env::var("PATH").unwrap_or_default();
        c.env("PATH", format!("/opt/homebrew/bin:/usr/local/bin:{home}/.local/bin:{home}/.cargo/bin:{path}"));
    }
    c
}

pub fn make_args(checkout: &str, target: &str) -> Result<Vec<String>, String> {
    if !matches!(target, "secrets" | "up" | "down") {
        return Err(format!("make target {target} is not allowed"));
    }
    Ok(vec!["-C".into(), checkout.into(), target.into()])
}

#[derive(Debug, PartialEq)]
pub struct Container {
    pub state: String,
    pub health: String,
    pub exit_code: i64,
}

pub fn parse_ps(out: &str) -> Vec<Container> {
    let items: Vec<serde_json::Value> = serde_json::from_str(out.trim())
        .unwrap_or_else(|_| out.lines().filter_map(|l| serde_json::from_str(l).ok()).collect());
    items
        .iter()
        .map(|v| {
            let s = |k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
            Container { state: s("State"), health: s("Health"), exit_code: v.get("ExitCode").and_then(|x| x.as_i64()).unwrap_or(0) }
        })
        .collect()
}

pub fn classify(containers: &[Container]) -> &'static str {
    let relevant: Vec<_> = containers.iter().filter(|c| !(c.state == "exited" && c.exit_code == 0)).collect();
    if relevant.is_empty() {
        return "stopped";
    }
    let running = relevant.iter().filter(|c| c.state == "running").count();
    if relevant.iter().any(|c| matches!(c.state.as_str(), "restarting" | "created") || c.health == "starting") {
        "starting"
    } else if running == relevant.len() {
        "running"
    } else if running > 0 {
        "partial"
    } else {
        "stopped"
    }
}

#[derive(Serialize)]
pub struct StackState {
    state: &'static str,
    ready: bool,
}

async fn pump<R: AsyncRead + Unpin>(r: R, app: &AppHandle, event: &str) {
    let mut r = BufReader::new(r);
    let mut buf = Vec::new();
    while matches!(r.read_until(b'\n', &mut buf).await, Ok(n) if n > 0) {
        let _ = app.emit(event, String::from_utf8_lossy(&buf).trim_end().to_string());
        buf.clear();
    }
}

pub async fn run_streamed(app: &AppHandle, event: &str, mut cmd: Command) -> Result<i32, String> {
    cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true);
    let mut child = cmd.spawn().map_err(|e| format!("cannot start command: {e}"))?;
    let (out, err) = (child.stdout.take().unwrap(), child.stderr.take().unwrap());
    tokio::join!(pump(out, app, event), pump(err, app, event));
    Ok(child.wait().await.map_err(|e| e.to_string())?.code().unwrap_or(-1))
}

async fn make(app: &AppHandle, checkout: &str, target: &str) -> Result<i32, String> {
    let mut cmd = command("make");
    cmd.args(make_args(checkout, target)?);
    run_streamed(app, "stack-log", cmd).await
}

pub async fn ensure_secrets(app: &AppHandle, checkout: &str) -> Result<(), String> {
    if std::path::Path::new(checkout).join("deploy/secrets/gateway.env").is_file() {
        return Ok(());
    }
    match make(app, checkout, "secrets").await? {
        0 => Ok(()),
        code => Err(format!("make secrets failed with exit code {code}")),
    }
}

fn local_checkout(state: &AppState, id: &str) -> Result<(String, String), String> {
    let p = store::find(&state.dir, id)?;
    if p.mode != "local" {
        return Err("project is not local".into());
    }
    let c = store::valid_checkout(p.checkout.as_deref().ok_or("project has no checkout")?)?;
    Ok((c.to_string_lossy().into_owned(), p.url))
}

#[tauri::command]
pub async fn stack_state(state: State<'_, AppState>, project_id: String) -> Result<StackState, String> {
    let p = store::find(&state.dir, &project_id)?;
    let ready = crate::api::client(3).get(format!("{}/readyz", p.url)).send().await.is_ok_and(|r| r.status() == 200);
    if p.mode != "local" {
        return Ok(StackState { state: "remote", ready });
    }
    let (checkout, _) = local_checkout(&state, &project_id)?;
    if state.starting.lock().unwrap().contains(&project_id) {
        return Ok(StackState { state: "starting", ready });
    }
    let out = command("docker")
        .args(["compose", "-f", "deploy/docker-compose.yml", "ps", "-a", "--format", "json"])
        .current_dir(checkout)
        .output()
        .await
        .map_err(|e| format!("cannot run docker: {e}"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    Ok(StackState { state: classify(&parse_ps(&String::from_utf8_lossy(&out.stdout))), ready })
}

async fn lifecycle(app: AppHandle, state: State<'_, AppState>, project_id: String, up: bool) -> Result<i32, String> {
    let (checkout, _) = local_checkout(&state, &project_id)?;
    if !state.starting.lock().unwrap().insert(project_id.clone()) {
        return Err("another stack command is running".into());
    }
    let result = async {
        if up {
            ensure_secrets(&app, &checkout).await?;
        }
        make(&app, &checkout, if up { "up" } else { "down" }).await
    }
    .await;
    state.starting.lock().unwrap().remove(&project_id);
    result
}

#[tauri::command]
pub async fn stack_up(app: AppHandle, state: State<'_, AppState>, project_id: String) -> Result<i32, String> {
    lifecycle(app, state, project_id, true).await
}

#[tauri::command]
pub async fn stack_down(app: AppHandle, state: State<'_, AppState>, project_id: String) -> Result<i32, String> {
    lifecycle(app, state, project_id, false).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn make_args_allow_list() {
        assert_eq!(make_args("/c", "up").unwrap(), ["-C", "/c", "up"]);
        assert!(make_args("/c", "clean").is_err());
        assert!(make_args("/c", "up; rm -rf /").is_err());
    }

    #[test]
    fn parses_json_lines_and_array() {
        let lines = "{\"State\":\"running\",\"Health\":\"healthy\"}\n{\"State\":\"exited\",\"ExitCode\":1}\n";
        let c = parse_ps(lines);
        assert_eq!(c.len(), 2);
        assert_eq!(c[1].exit_code, 1);
        assert_eq!(parse_ps("[{\"State\":\"running\"}]").len(), 1);
        assert!(parse_ps("").is_empty());
    }

    #[test]
    fn classifies_states() {
        let c = |s: &str, h: &str, e: i64| Container { state: s.into(), health: h.into(), exit_code: e };
        assert_eq!(classify(&[]), "stopped");
        assert_eq!(classify(&[c("running", "healthy", 0), c("exited", "", 0)]), "running");
        assert_eq!(classify(&[c("running", "starting", 0)]), "starting");
        assert_eq!(classify(&[c("running", "", 0), c("exited", "", 1)]), "partial");
        assert_eq!(classify(&[c("exited", "", 1)]), "stopped");
    }
}
