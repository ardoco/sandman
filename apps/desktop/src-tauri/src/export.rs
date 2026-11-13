use crate::{api, errcode::coded, ingest::cli_parts, stack, store, AppState};
use serde::Serialize;
use serde_json::json;
use std::{fs, path::Path, process::Stdio};
use tauri::State;

#[derive(Serialize, Debug, PartialEq)]
pub struct Exported {
    rows: usize,
    path: String,
}

pub const BENCHMARK_FORMATS: [&str; 4] = [
    "ardoco-sad-sam",
    "ardoco-sad-code",
    "ardoco-sam-code",
    "lissa",
];

fn bad_option(name: &str) -> String {
    coded(
        "export_option",
        json!({"name": name}),
        format!("invalid export option: {name}"),
    )
}

fn valid_approach(a: &str) -> bool {
    !a.is_empty() && a.len() <= 200 && !a.chars().any(char::is_control)
}

pub fn query(
    what: &str,
    format: &str,
    approach: Option<&str>,
    min_confidence: Option<f64>,
    include_rejected: Option<bool>,
) -> Result<(String, Vec<(String, String)>), String> {
    if !matches!(format, "csv" | "json") {
        return Err(bad_option("format"));
    }
    let mut pairs = vec![("format".to_string(), format.to_string())];
    let route = match what {
        "tracelinks" => "tracelinks",
        "findings" => "inconsistencies",
        _ => return Err(bad_option("what")),
    };
    if what == "tracelinks" {
        if let Some(a) = approach.filter(|a| !a.is_empty()) {
            if !valid_approach(a) {
                return Err(bad_option("approach"));
            }
            pairs.push(("approach".into(), a.into()));
        }
        if let Some(c) = min_confidence.filter(|c| *c != 0.0) {
            if !(0.0..=1.0).contains(&c) {
                return Err(bad_option("minConfidence"));
            }
            pairs.push(("minConfidence".into(), c.to_string()));
        }
        if include_rejected == Some(true) {
            pairs.push(("includeRejected".into(), "true".into()));
        }
    }
    Ok((route.to_string(), pairs))
}

pub fn count_rows(format: &str, body: &str) -> usize {
    if format == "json" {
        return serde_json::from_str::<Vec<serde_json::Value>>(body).map_or(0, |v| v.len());
    }
    body.lines()
        .filter(|l| !l.trim().is_empty())
        .count()
        .saturating_sub(1)
}

pub fn write_target(path: &str, body: &[u8]) -> Result<(), String> {
    let p = Path::new(path);
    if !p.is_absolute() || p.is_dir() || !p.parent().is_some_and(Path::is_dir) {
        return Err(coded(
            "export_path",
            json!({}),
            "the target must be a file path in an existing folder",
        ));
    }
    fs::write(p, body).map_err(|e| {
        coded(
            "export_write",
            json!({"detail": e.to_string()}),
            format!("cannot write the file: {e}"),
        )
    })
}

fn finish(format: &str, body: &[u8], path: &str) -> Result<Exported, String> {
    write_target(path, body)?;
    Ok(Exported {
        rows: count_rows(format, &String::from_utf8_lossy(body)),
        path: path.to_string(),
    })
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
pub async fn export_generic(
    state: State<'_, AppState>,
    project_id: String,
    what: String,
    format: String,
    approach: Option<String>,
    min_confidence: Option<f64>,
    include_rejected: Option<bool>,
    path: String,
) -> Result<Exported, String> {
    let (route, pairs) = query(
        &what,
        &format,
        approach.as_deref(),
        min_confidence,
        include_rejected,
    )?;
    let project = store::find(&state.dir, &project_id)?;
    let token = api::token(&state, &project_id)?;
    let url = format!(
        "{}/v1/projects/{}/export/{}",
        project.url, project_id, route
    );
    let resp = api::client(120)
        .get(url)
        .bearer_auth(token)
        .query(&pairs)
        .send()
        .await
        .map_err(api::not_reachable)?;
    let status = resp.status().as_u16();
    let bytes = resp
        .bytes()
        .await
        .map_err(|e| e.without_url().to_string())?;
    if status >= 400 {
        let detail: String = String::from_utf8_lossy(&bytes).chars().take(300).collect();
        let code = if matches!(status, 401 | 403) {
            "export_auth"
        } else {
            "export_http"
        };
        return Err(coded(
            code,
            json!({"status": status, "detail": detail}),
            format!("HTTP {status}: {detail}"),
        ));
    }
    finish(&format, &bytes, &path)
}

pub fn benchmark_args(
    cli: Option<&str>,
    checkout: Option<&str>,
    project: &str,
    format: &str,
    approach: Option<&str>,
    code_root: Option<&str>,
) -> Result<(String, Vec<String>), String> {
    if !BENCHMARK_FORMATS.contains(&format) {
        return Err(bad_option("format"));
    }
    let mut parts = cli_parts(cli, checkout)?;
    parts.extend(
        [
            "export",
            "tracelinks",
            "--format",
            format,
            "--project",
            project,
        ]
        .map(String::from),
    );
    if let Some(a) = approach.filter(|a| !a.is_empty()) {
        if !valid_approach(a) {
            return Err(bad_option("approach"));
        }
        parts.push(format!("--approach={a}"));
    }
    if let Some(r) = code_root.filter(|r| !r.is_empty() && format == "ardoco-sad-code") {
        if r.len() > 500 || r.chars().any(char::is_control) {
            return Err(bad_option("codeRoot"));
        }
        parts.push(format!("--code-root={r}"));
    }
    Ok((parts.remove(0), parts))
}

pub fn benchmark_env(token: &str, url: &str) -> [(&'static str, String); 2] {
    [
        ("SANDMAN_TOKEN", token.to_string()),
        ("SANDMAN_URL", url.to_string()),
    ]
}

pub fn tail_lines(text: &str, n: usize) -> String {
    let lines: Vec<&str> = text.trim().lines().collect();
    lines[lines.len().saturating_sub(n)..].join("\n")
}

#[tauri::command]
pub async fn export_benchmark(
    state: State<'_, AppState>,
    project_id: String,
    format: String,
    approach: Option<String>,
    code_root: Option<String>,
    path: String,
) -> Result<Exported, String> {
    let project = store::find(&state.dir, &project_id)?;
    let (program, args) = benchmark_args(
        project.cli_command.as_deref(),
        project.checkout.as_deref(),
        &project_id,
        &format,
        approach.as_deref(),
        code_root.as_deref(),
    )?;
    let token = api::token(&state, &project_id)?;
    let mut cmd = stack::command(&program);
    cmd.args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    for (k, v) in benchmark_env(&token, &project.url) {
        cmd.env(k, v);
    }
    if let Some(c) = &project.checkout {
        cmd.current_dir(c);
    }
    let child = cmd.spawn().map_err(|e| stack::spawn_error(&program, &e))?;
    let out = child.wait_with_output().await.map_err(|e| e.to_string())?;
    if !out.status.success() {
        let lines = tail_lines(&String::from_utf8_lossy(&out.stderr), 15);
        let code = out.status.code().unwrap_or(-1);
        return Err(coded(
            "export_cli_failed",
            json!({"code": code, "lines": lines}),
            format!("sandman export failed (exit {code}):\n{lines}"),
        ));
    }
    finish("csv", &out.stdout, &path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pairs(v: &[(&str, &str)]) -> Vec<(String, String)> {
        v.iter()
            .map(|(a, b)| (a.to_string(), b.to_string()))
            .collect()
    }

    #[test]
    fn query_omits_defaults_and_keeps_set_options() {
        let (route, q) = query("tracelinks", "csv", Some(""), Some(0.0), Some(false)).unwrap();
        assert_eq!(
            (route.as_str(), q),
            ("tracelinks", pairs(&[("format", "csv")]))
        );
        let (_, q) = query(
            "tracelinks",
            "json",
            Some("ardoco tlr&x"),
            Some(0.5),
            Some(true),
        )
        .unwrap();
        assert_eq!(
            q,
            pairs(&[
                ("format", "json"),
                ("approach", "ardoco tlr&x"),
                ("minConfidence", "0.5"),
                ("includeRejected", "true")
            ])
        );
    }

    #[test]
    fn query_for_findings_ignores_link_options_and_uses_the_inconsistency_route() {
        let (route, q) = query("findings", "json", Some("a"), Some(0.9), Some(true)).unwrap();
        assert_eq!(
            (route.as_str(), q),
            ("inconsistencies", pairs(&[("format", "json")]))
        );
    }

    #[test]
    fn query_rejects_bad_values() {
        assert!(query("tracelinks", "xml", None, None, None).is_err());
        assert!(query("other", "csv", None, None, None).is_err());
        assert!(query("tracelinks", "csv", None, Some(1.5), None).is_err());
        assert!(query("tracelinks", "csv", None, Some(f64::NAN), None).is_err());
        assert!(query("tracelinks", "csv", Some("a\nb"), None, None).is_err());
    }

    #[test]
    fn counts_rows_without_header_and_blank_lines() {
        assert_eq!(count_rows("csv", "a,b\n1,2\n3,4\n\n"), 2);
        assert_eq!(count_rows("csv", "a,b\n"), 0);
        assert_eq!(count_rows("csv", ""), 0);
        assert_eq!(count_rows("json", "[{\"a\":1},{\"a\":2}]"), 2);
        assert_eq!(count_rows("json", "not json"), 0);
    }

    #[test]
    fn writes_the_body_into_an_existing_folder_only() {
        let d = tempfile::tempdir().unwrap();
        let file = d.path().join("out.csv");
        let r = finish("csv", b"h\n1\n2\n", &file.to_string_lossy()).unwrap();
        assert_eq!(
            r,
            Exported {
                rows: 2,
                path: file.to_string_lossy().into_owned()
            }
        );
        assert_eq!(fs::read_to_string(&file).unwrap(), "h\n1\n2\n");
        assert!(write_target(&d.path().to_string_lossy(), b"x").is_err());
        assert!(write_target(&d.path().join("no/dir.csv").to_string_lossy(), b"x").is_err());
        assert!(write_target("relative.csv", b"x").is_err());
    }

    #[test]
    fn benchmark_args_hold_no_token_and_gate_the_options() {
        let (p, a) = benchmark_args(
            None,
            Some("/co"),
            "demo",
            "ardoco-sad-code",
            Some("ardoco"),
            Some("src/"),
        )
        .unwrap();
        assert_eq!(p, "uv");
        assert_eq!(a.join(" "), "run --project /co sandman export tracelinks --format ardoco-sad-code --project demo --approach=ardoco --code-root=src/");
        let (_, a) = benchmark_args(
            Some("sandman"),
            None,
            "demo",
            "lissa",
            Some(""),
            Some("src/"),
        )
        .unwrap();
        assert_eq!(
            a.join(" "),
            "export tracelinks --format lissa --project demo"
        );
        assert!(benchmark_args(None, None, "demo", "lissa", None, None).is_err());
        assert!(benchmark_args(Some("sandman"), None, "demo", "csv", None, None).is_err());
        assert!(!a.iter().any(|x| x.contains("tok-123")));
    }

    #[test]
    fn child_environment_carries_token_and_url() {
        let env = benchmark_env("tok-123", "http://h");
        assert_eq!(
            env.iter().map(|(k, _)| *k).collect::<Vec<_>>(),
            vec!["SANDMAN_TOKEN", "SANDMAN_URL"]
        );
        assert_eq!(env[0].1, "tok-123");
    }

    #[test]
    fn tail_keeps_the_last_lines() {
        assert_eq!(tail_lines("a\nb\nc\n", 2), "b\nc");
        assert_eq!(tail_lines("", 2), "");
    }
}
