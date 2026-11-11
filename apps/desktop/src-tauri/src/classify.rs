use crate::{ingest::cli_parts, stack, store, AppState};
use serde::{Deserialize, Serialize};
use std::{fs, process::Stdio};
use tauri::State;
use tokio::io::AsyncWriteExt;

#[derive(Serialize, Deserialize)]
pub struct Edit {
    pub path: String,
    pub kind: String,
}

pub fn classify_args(
    cli: Option<&str>,
    checkout: Option<&str>,
    dir: &str,
    rules: Option<bool>,
) -> Result<(String, Vec<String>), String> {
    let mut parts = cli_parts(cli, checkout)?;
    parts.push("classify".into());
    match rules {
        None => parts.extend(["--json", "--all"].map(String::from)),
        Some(write) => {
            parts.push("--rules-stdin".into());
            if write {
                parts.push("--write".into());
            }
        }
    }
    parts.extend(["--".to_string(), dir.to_string()]);
    Ok((parts.remove(0), parts))
}

pub fn edits_json(edits: &[Edit]) -> String {
    serde_json::json!({ "edits": edits }).to_string()
}

async fn run(state: &AppState, project_id: &str, dir: &str, rules: Option<(Vec<Edit>, bool)>) -> Result<String, String> {
    let project = store::find(&state.dir, project_id)?;
    let dir = fs::canonicalize(dir).map_err(|e| format!("path: {e}"))?;
    if !dir.is_dir() {
        return Err("pick a folder".into());
    }
    let (program, args) = classify_args(
        project.cli_command.as_deref(),
        project.checkout.as_deref(),
        &dir.to_string_lossy(),
        rules.as_ref().map(|r| r.1),
    )?;
    let mut cmd = stack::command(&program);
    cmd.args(args).stdin(if rules.is_some() { Stdio::piped() } else { Stdio::null() });
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true);
    if let Some(c) = &project.checkout {
        cmd.current_dir(c);
    }
    let mut child = cmd.spawn().map_err(|e| stack::spawn_error(&program, &e))?;
    if let Some((edits, _)) = &rules {
        let mut stdin = child.stdin.take().ok_or("no stdin")?;
        stdin.write_all(edits_json(edits).as_bytes()).await.map_err(|e| e.to_string())?;
    }
    let out = child.wait_with_output().await.map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        let err = String::from_utf8_lossy(&out.stderr);
        Err(format!("sandman classify failed (exit {}): {}", out.status.code().unwrap_or(-1), err.trim()))
    }
}

#[tauri::command]
pub async fn classify_scan(state: State<'_, AppState>, project_id: String, dir: String) -> Result<String, String> {
    run(&state, &project_id, &dir, None).await
}

#[tauri::command]
pub async fn classify_rules(
    state: State<'_, AppState>,
    project_id: String,
    dir: String,
    edits: Vec<Edit>,
    write: bool,
) -> Result<String, String> {
    run(&state, &project_id, &dir, Some((edits, write))).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_scan_and_rules_commands() {
        let (p, a) = classify_args(None, Some("/co"), "/d", None).unwrap();
        assert_eq!(p, "uv");
        assert_eq!(a.join(" "), "run --project /co sandman classify --json --all -- /d");
        let (_, a) = classify_args(Some("uvx sandman"), None, "/d", Some(false)).unwrap();
        assert_eq!(a.join(" "), "sandman classify --rules-stdin -- /d");
        let (_, a) = classify_args(Some("sandman"), None, "/d", Some(true)).unwrap();
        assert_eq!(a.join(" "), "classify --rules-stdin --write -- /d");
        assert!(classify_args(None, None, "/d", None).is_err());
    }

    #[test]
    fn serializes_edits_for_stdin() {
        let e = [Edit { path: "docs".into(), kind: "other".into() }];
        assert_eq!(edits_json(&e), r#"{"edits":[{"kind":"other","path":"docs"}]}"#);
    }
}
