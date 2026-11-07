use crate::{store, AppState};
use serde::Serialize;
use std::{collections::VecDeque, process::Stdio, sync::Arc};
use tauri::{AppHandle, Emitter, State};
use tokio::sync::Notify;
use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};
use tokio::process::Command;

pub fn command(program: &str) -> Command {
    let mut c = Command::new(program);
    if cfg!(unix) {
        c.env("PATH", search_path());
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

pub const TAIL_LINES: usize = 30;
pub const CANCELLED: i32 = 130;

async fn pump<R: AsyncRead + Unpin>(r: R, app: &AppHandle, event: &str, tail: &std::sync::Mutex<VecDeque<String>>) {
    let mut r = BufReader::new(r);
    let mut buf = Vec::new();
    while matches!(r.read_until(b'\n', &mut buf).await, Ok(n) if n > 0) {
        let line = String::from_utf8_lossy(&buf).trim_end().to_string();
        let _ = app.emit(event, line.clone());
        let mut t = tail.lock().unwrap();
        if t.len() == TAIL_LINES {
            t.pop_front();
        }
        t.push_back(line);
        buf.clear();
    }
}

pub struct Run {
    pub code: i32,
    pub tail: String,
}

pub fn missing_program_message(program: &str) -> String {
    let hint = match program {
        "make" => "Install the Xcode command line tools (xcode-select --install) or your package manager's make.",
        "docker" => "Install Docker Desktop or Docker Engine and start it.",
        "uv" => "Install uv (https://docs.astral.sh/uv/) or set the CLI command in the project settings.",
        _ => "Install it and restart the app.",
    };
    format!("`{program}` was not found. {hint}")
}

pub fn spawn_error(program: &str, e: &std::io::Error) -> String {
    if e.kind() == std::io::ErrorKind::NotFound {
        missing_program_message(program)
    } else {
        format!("cannot start `{program}`: {e}")
    }
}

pub async fn run_streamed(app: &AppHandle, event: &str, mut cmd: Command, cancel: Option<Arc<Notify>>) -> Result<Run, String> {
    cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true);
    let program = cmd.as_std().get_program().to_string_lossy().into_owned();
    let mut child = cmd.spawn().map_err(|e| spawn_error(&program, &e))?;
    let (out, err) = (child.stdout.take().unwrap(), child.stderr.take().unwrap());
    let tail = std::sync::Mutex::new(VecDeque::new());
    let io = async { tokio::join!(pump(out, app, event, &tail), pump(err, app, event, &tail)) };
    tokio::pin!(io);
    let cancelled = async {
        match &cancel {
            Some(n) => n.notified().await,
            None => std::future::pending().await,
        }
    };
    let mut was_cancelled = false;
    tokio::select! {
        _ = &mut io => {}
        _ = cancelled => {
            was_cancelled = true;
            let _ = child.kill().await;
            (&mut io).await;
        }
    }
    let status = child.wait().await.map_err(|e| e.to_string())?;
    let code = if was_cancelled { CANCELLED } else { status.code().unwrap_or(-1) };
    let tail = tail.lock().unwrap().iter().cloned().collect::<Vec<_>>().join("\n");
    Ok(Run { code, tail })
}

async fn make(app: &AppHandle, checkout: &str, target: &str) -> Result<Run, String> {
    let mut cmd = command("make");
    cmd.args(make_args(checkout, target)?);
    run_streamed(app, "stack-log", cmd, None).await
}

pub async fn ensure_secrets(app: &AppHandle, checkout: &str) -> Result<(), String> {
    if std::path::Path::new(checkout).join("deploy/secrets/gateway.env").is_file() {
        return Ok(());
    }
    match make(app, checkout, "secrets").await? {
        Run { code: 0, .. } => Ok(()),
        Run { code, tail } => Err(format!("make secrets failed with exit code {code}.\n{tail}")),
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

async fn containers(checkout: &str) -> Result<Vec<Container>, String> {
    let out = command("docker")
        .args(["compose", "-f", "deploy/docker-compose.yml", "ps", "-a", "--format", "json"])
        .current_dir(checkout)
        .output()
        .await
        .map_err(|e| spawn_error("docker", &e))?;
    if !out.status.success() {
        return Err(diagnose(&String::from_utf8_lossy(&out.stderr)).unwrap_or_else(|| String::from_utf8_lossy(&out.stderr).trim().to_string()));
    }
    Ok(parse_ps(&String::from_utf8_lossy(&out.stdout)))
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
    Ok(StackState { state: classify(&containers(&checkout).await?), ready })
}

pub fn port_of(url: &str) -> Option<u16> {
    url::Url::parse(url).ok()?.port_or_known_default()
}

pub fn port_busy(port: u16) -> bool {
    std::net::TcpListener::bind(("127.0.0.1", port)).is_err()
}

pub fn find_in_path(name: &str, path: &str) -> bool {
    std::env::split_paths(path).any(|d| d.join(name).is_file())
}

pub fn search_path() -> String {
    let home = std::env::var("HOME").unwrap_or_default();
    let path = std::env::var("PATH").unwrap_or_default();
    format!("/opt/homebrew/bin:/usr/local/bin:{home}/.local/bin:{home}/.cargo/bin:{path}")
}

#[tauri::command]
pub fn missing_tools(names: Vec<String>) -> Result<Vec<String>, String> {
    if let Some(n) = names.iter().find(|n| !matches!(n.as_str(), "make" | "docker" | "uv")) {
        return Err(format!("tool {n} is not allowed"));
    }
    let path = search_path();
    Ok(names.into_iter().filter(|n| !find_in_path(n, &path)).collect())
}

pub fn diagnose(log: &str) -> Option<String> {
    let l = log.to_lowercase();
    let msg = if l.contains("cannot connect to the docker daemon") || l.contains("is the docker daemon running") {
        "Docker is not running. Start Docker Desktop and try again."
    } else if l.contains("port is already allocated") || l.contains("address already in use") || l.contains("ports are not available") {
        "A port is already in use by another program. Stop that program and try again. The system needs port 8080."
    } else if l.contains("check-env:") {
        let line = log.lines().rev().find(|x| x.contains("check-env:")).unwrap_or_default();
        return Some(format!("The preflight check failed: {}", line.trim()));
    } else if l.contains("no space left on device") {
        "Docker has no disk space left. Free space or run docker system prune."
    } else {
        return None;
    };
    Some(msg.to_string())
}

#[derive(Serialize)]
pub struct Lifecycle {
    code: i32,
    hint: Option<String>,
}

async fn lifecycle(app: AppHandle, state: State<'_, AppState>, project_id: String, up: bool) -> Result<Lifecycle, String> {
    let (checkout, url) = local_checkout(&state, &project_id)?;
    if !state.starting.lock().unwrap().insert(project_id.clone()) {
        return Err("another stack command is running".into());
    }
    let result = async {
        if up {
            if let Some(port) = port_of(&url).filter(|p| port_busy(*p)) {
                let ours = containers(&checkout).await.map(|c| classify(&c) != "stopped").unwrap_or(false);
                if !ours {
                    return Err(format!("Port {port} is in use by another program. Stop that program and try again."));
                }
            }
            ensure_secrets(&app, &checkout).await?;
        }
        let run = make(&app, &checkout, if up { "up" } else { "down" }).await?;
        let hint = if run.code == 0 { None } else { diagnose(&run.tail) };
        Ok(Lifecycle { code: run.code, hint })
    }
    .await;
    state.starting.lock().unwrap().remove(&project_id);
    result
}

#[tauri::command]
pub async fn stack_up(app: AppHandle, state: State<'_, AppState>, project_id: String) -> Result<Lifecycle, String> {
    lifecycle(app, state, project_id, true).await
}

#[tauri::command]
pub async fn stack_down(app: AppHandle, state: State<'_, AppState>, project_id: String) -> Result<Lifecycle, String> {
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
    fn diagnoses_known_failures() {
        assert!(diagnose("Cannot connect to the Docker daemon at unix:///var/run/docker.sock").unwrap().contains("Docker is not running"));
        assert!(diagnose("Bind for 0.0.0.0:8080 failed: port is already allocated").unwrap().contains("port"));
        let d = diagnose("x\ncheck-env: deploy/secrets/corenlp_credentials missing; run 'make secrets'\n").unwrap();
        assert!(d.contains("corenlp_credentials missing"));
        assert_eq!(diagnose("all fine"), None);
    }

    #[test]
    fn names_missing_programs() {
        let e = std::io::Error::from(std::io::ErrorKind::NotFound);
        assert!(spawn_error("make", &e).contains("`make` was not found"));
        assert!(spawn_error("docker", &e).contains("Docker Desktop"));
        let other = std::io::Error::from(std::io::ErrorKind::PermissionDenied);
        assert!(spawn_error("make", &other).starts_with("cannot start"));
    }

    #[test]
    fn finds_tools_in_path_and_checks_ports() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("make"), "").unwrap();
        let path = d.path().to_string_lossy().into_owned();
        assert!(find_in_path("make", &path));
        assert!(!find_in_path("docker", &path));
        assert_eq!(missing_tools(vec!["nope".into()]).unwrap_err(), "tool nope is not allowed");
        assert_eq!(port_of("http://127.0.0.1:8080"), Some(8080));
        let l = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        assert!(port_busy(l.local_addr().unwrap().port()));
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
