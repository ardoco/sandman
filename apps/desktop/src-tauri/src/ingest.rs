use crate::{api, stack, store, AppState};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::Arc,
};
use tokio::sync::Notify;
use tauri::{AppHandle, Emitter, State};

pub fn common_parent(files: &[PathBuf]) -> PathBuf {
    let mut base = files[0].parent().map(Path::to_path_buf).unwrap_or_default();
    while !files.iter().all(|f| f.starts_with(&base)) {
        base.pop();
    }
    base
}

pub fn stage_files(files: &[PathBuf], dest: &Path) -> std::io::Result<()> {
    let base = common_parent(files);
    for f in files {
        let target = dest.join(f.strip_prefix(&base).expect("below common parent"));
        fs::create_dir_all(target.parent().expect("has parent"))?;
        fs::copy(f, target)?;
    }
    Ok(())
}

pub fn cli_parts(cli: Option<&str>, checkout: Option<&str>) -> Result<Vec<String>, String> {
    match (cli, checkout) {
        (Some(c), _) if !c.trim().is_empty() => Ok(c.split_whitespace().map(String::from).collect()),
        (_, Some(co)) => Ok(["uv", "run", "--project", co, "sandman"].map(String::from).to_vec()),
        _ => Err("set the CLI command in the project settings".into()),
    }
}

pub fn build_command(
    cli: Option<&str>,
    checkout: Option<&str>,
    project: &str,
    mode: &str,
    revision: Option<&str>,
    no_review: bool,
    dir: &str,
) -> Result<(String, Vec<String>), String> {
    let mut parts = cli_parts(cli, checkout)?;
    parts.extend(["ingest", "--project", project, "--mode", mode].map(String::from));
    if let Some(r) = revision {
        parts.push(format!("--revision={r}"));
    }
    if no_review {
        parts.push("--no-review".into());
    }
    parts.extend(["--".to_string(), dir.to_string()]);
    Ok((parts.remove(0), parts))
}

#[tauri::command]
pub async fn ingest(
    app: AppHandle,
    state: State<'_, AppState>,
    project_id: String,
    paths: Vec<String>,
    mode: String,
    revision: Option<String>,
    no_review: Option<bool>,
) -> Result<i32, String> {
    if !matches!(mode.as_str(), "full" | "partial") {
        return Err("mode must be full or partial".into());
    }
    let revision = revision.filter(|r| !r.trim().is_empty());
    if revision.as_deref().is_some_and(|r| r.len() > 200 || r.chars().any(char::is_control)) {
        return Err("invalid revision".into());
    }
    let project = store::find(&state.dir, &project_id)?;
    let token = api::token(&state, &project_id)?;
    let paths: Vec<PathBuf> = paths.iter().map(fs::canonicalize).collect::<Result<_, _>>().map_err(|e| format!("path: {e}"))?;
    let stage = tempfile::tempdir().map_err(|e| e.to_string())?;
    let (dir, mode) = match paths.as_slice() {
        [] => return Err("no path given".into()),
        [d] if d.is_dir() => (d.clone(), mode),
        files if files.iter().all(|f| f.is_file()) => {
            stage_files(files, stage.path()).map_err(|e| e.to_string())?;
            (stage.path().to_path_buf(), "partial".to_string())
        }
        _ => return Err("pick one folder or one or more files".into()),
    };
    let (program, args) = build_command(
        project.cli_command.as_deref(),
        project.checkout.as_deref(),
        &project_id,
        &mode,
        revision.as_deref(),
        no_review.unwrap_or(false) || dir == stage.path(),
        &dir.to_string_lossy(),
    )?;
    let _ = app.emit("ingest-log", format!("$ {program} {}", args.join(" ")));
    let mut cmd = stack::command(&program);
    cmd.args(args).env("SANDMAN_TOKEN", token).env("SANDMAN_URL", &project.url);
    if let Some(c) = &project.checkout {
        cmd.current_dir(c);
    }
    let cancel = Arc::new(Notify::new());
    state.ingests.lock().unwrap().insert(project_id.clone(), cancel.clone());
    let run = stack::run_streamed(&app, "ingest-log", cmd, Some(cancel)).await;
    state.ingests.lock().unwrap().remove(&project_id);
    Ok(run?.code)
}

#[tauri::command]
pub fn ingest_cancel(state: State<'_, AppState>, project_id: String) {
    if let Some(n) = state.ingests.lock().unwrap().get(&project_id) {
        n.notify_one();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn staged_layout_keeps_paths_below_common_parent() {
        let src = tempfile::tempdir().unwrap();
        let a = src.path().join("docs/a.md");
        let b = src.path().join("docs/sub/b.md");
        fs::create_dir_all(b.parent().unwrap()).unwrap();
        fs::write(&a, "a").unwrap();
        fs::write(&b, "b").unwrap();
        let dest = tempfile::tempdir().unwrap();
        stage_files(&[a.clone(), b], dest.path()).unwrap();
        assert_eq!(fs::read_to_string(dest.path().join("a.md")).unwrap(), "a");
        assert_eq!(fs::read_to_string(dest.path().join("sub/b.md")).unwrap(), "b");
        let one = tempfile::tempdir().unwrap();
        stage_files(&[a], one.path()).unwrap();
        assert!(one.path().join("a.md").is_file());
    }

    #[test]
    fn builds_uv_and_custom_commands() {
        let (p, a) = build_command(None, Some("/co"), "demo", "full", Some("abc"), false, "/dir").unwrap();
        assert_eq!(p, "uv");
        assert_eq!(a.join(" "), "run --project /co sandman ingest --project demo --mode full --revision=abc -- /dir");
        let (p, a) = build_command(Some("uvx  sandman"), None, "demo", "partial", None, true, "/d").unwrap();
        assert_eq!((p.as_str(), a.join(" ")), ("uvx", "sandman ingest --project demo --mode partial --no-review -- /d".to_string()));
        assert!(build_command(None, None, "demo", "full", None, false, "/d").is_err());
    }
}
