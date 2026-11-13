use crate::errcode::coded;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

pub const SERVICE: &str = "sandman-desktop";
pub const LOCAL_URL: &str = "http://127.0.0.1:8080";

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Project {
    pub id: String,
    pub name: String,
    pub mode: String,
    pub url: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub checkout: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cli_command: Option<String>,
    #[serde(default)]
    pub last_used: u64,
}

pub fn valid_slug(id: &str) -> bool {
    let b = id.as_bytes();
    (2..=63).contains(&b.len())
        && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit())
        && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-')
}

fn is_loopback(host: &str) -> bool {
    matches!(host, "localhost" | "127.0.0.1" | "[::1]" | "::1")
}

pub fn valid_url(raw: &str) -> Result<(String, Option<String>), String> {
    let u = url::Url::parse(raw.trim()).map_err(|e| coded("url_invalid", json!({"detail": e.to_string()}), format!("invalid URL: {e}")))?;
    if !matches!(u.scheme(), "http" | "https") {
        return Err(coded("url_scheme", json!({}), "URL must use http or https"));
    }
    let host = u.host_str().ok_or_else(|| coded("url_host", json!({}), "URL has no host"))?;
    if !u.username().is_empty() || u.password().is_some() || u.query().is_some() || u.fragment().is_some() {
        return Err(coded("url_extras", json!({}), "URL must not contain credentials, a query or a fragment"));
    }
    let warning = (u.scheme() == "http" && !is_loopback(host))
        .then(|| coded("url_insecure", json!({}), "The token is sent without encryption. Use https for a remote system."));
    Ok((u.as_str().trim_end_matches('/').to_string(), warning))
}

pub fn valid_checkout(path: &str) -> Result<PathBuf, String> {
    let p = fs::canonicalize(path).map_err(|e| coded("checkout_path", json!({"detail": e.to_string()}), format!("checkout path: {e}")))?;
    if !p.join("deploy/docker-compose.yml").is_file() {
        return Err(coded("checkout_compose", json!({}), "checkout must contain deploy/docker-compose.yml"));
    }
    Ok(p)
}

fn no_control(s: &str) -> bool {
    !s.chars().any(char::is_control)
}

pub fn validate(p: &mut Project) -> Result<Option<String>, String> {
    if !valid_slug(&p.id) {
        return Err(coded("project_id", json!({}), "project id must match ^[a-z0-9][a-z0-9-]{1,62}$"));
    }
    if p.name.trim().is_empty() || p.name.len() > 100 || !no_control(&p.name) {
        return Err(coded("project_name", json!({}), "project name must have 1 to 100 characters"));
    }
    if p.cli_command.as_deref().is_some_and(|c| !no_control(c)) {
        return Err(coded("cli_control", json!({}), "CLI command must not contain control characters"));
    }
    p.cli_command = p.cli_command.take().filter(|c| !c.trim().is_empty());
    match p.mode.as_str() {
        "local" => {
            let c = valid_checkout(p.checkout.as_deref().ok_or_else(|| coded("checkout_needed", json!({}), "local project needs a checkout"))?)?;
            p.checkout = Some(c.to_string_lossy().into_owned());
            p.url = LOCAL_URL.into();
            Ok(None)
        }
        "remote" => {
            let (url, warning) = valid_url(&p.url)?;
            p.url = url;
            p.checkout = p.checkout.take().filter(|c| valid_checkout(c).is_ok());
            Ok(warning)
        }
        _ => Err(coded("mode_invalid", json!({}), "mode must be local or remote")),
    }
}

pub fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

pub fn load_projects(dir: &Path) -> Result<Vec<Project>, String> {
    match fs::read_to_string(dir.join("projects.json")) {
        Ok(s) => serde_json::from_str(&s).map_err(|e| format!("projects.json: {e}")),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(vec![]),
        Err(e) => Err(e.to_string()),
    }
}

pub fn write_json<T: Serialize>(dir: &Path, name: &str, v: &T) -> Result<(), String> {
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let s = serde_json::to_string_pretty(v).map_err(|e| e.to_string())?;
    fs::write(dir.join(name), s).map_err(|e| e.to_string())
}

pub fn upsert(dir: &Path, p: Project) -> Result<(), String> {
    let mut list = load_projects(dir)?;
    list.retain(|x| x.id != p.id);
    list.push(p);
    list.sort_by(|a, b| b.last_used.cmp(&a.last_used));
    write_json(dir, "projects.json", &list)
}

pub fn remove(dir: &Path, id: &str) -> Result<(), String> {
    let mut list = load_projects(dir)?;
    list.retain(|x| x.id != id);
    write_json(dir, "projects.json", &list)
}

pub fn find(dir: &Path, id: &str) -> Result<Project, String> {
    load_projects(dir)?.into_iter().find(|p| p.id == id).ok_or_else(|| format!("unknown project {id}"))
}

#[derive(Serialize, Deserialize, Default, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Defaults {
    #[serde(default)]
    pub selfhosted_llm_url: String,
    #[serde(default)]
    pub corenlp_mode: String,
}

pub fn load_defaults(dir: &Path) -> Defaults {
    fs::read_to_string(dir.join("defaults.json")).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

pub fn save_defaults(dir: &Path, d: &Defaults) -> Result<(), String> {
    write_json(dir, "defaults.json", d)
}

#[derive(Serialize, Deserialize, Default, Clone, Copy, Debug, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Language {
    #[default]
    System,
    En,
    De,
}

#[derive(Serialize, Deserialize, Default, Clone, Copy, Debug, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Theme {
    #[default]
    System,
    Light,
    Dark,
}

#[derive(Serialize, Deserialize, Default, Clone, Copy, Debug, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum FontSize {
    Small,
    #[default]
    Normal,
    Large,
}

fn lenient<'de, D, T>(d: D) -> Result<T, D::Error>
where
    D: serde::Deserializer<'de>,
    T: serde::de::DeserializeOwned + Default,
{
    Ok(serde_json::Value::deserialize(d)
        .ok()
        .and_then(|v| serde_json::from_value(v).ok())
        .unwrap_or_default())
}

#[derive(Serialize, Deserialize, Default, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct AppSettings {
    #[serde(default, deserialize_with = "lenient")]
    pub language: Language,
    #[serde(default, deserialize_with = "lenient")]
    pub theme: Theme,
    #[serde(default, deserialize_with = "lenient")]
    pub font_size: FontSize,
}

pub fn load_app_settings(dir: &Path) -> AppSettings {
    fs::read_to_string(dir.join("settings.json"))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

pub fn save_app_settings(dir: &Path, s: &AppSettings) -> Result<(), String> {
    write_json(dir, "settings.json", s)
}

fn entry(user: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(SERVICE, user).map_err(|e| format!("keychain: {e}"))
}

pub fn secret_set(user: &str, value: &str) -> Result<(), String> {
    entry(user)?.set_password(value).map_err(|e| format!("keychain: {e}"))
}

pub fn secret_get(user: &str) -> Result<Option<String>, String> {
    match entry(user)?.get_password() {
        Ok(v) => Ok(Some(v)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("keychain: {e}")),
    }
}

pub fn secret_delete(user: &str) -> Result<(), String> {
    match entry(user)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!("keychain: {e}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn project(id: &str, used: u64) -> Project {
        Project {
            id: id.into(),
            name: id.into(),
            mode: "remote".into(),
            url: "https://sandman.example".into(),
            checkout: None,
            cli_command: None,
            last_used: used,
        }
    }

    #[test]
    fn store_round_trip_sorted_by_last_used() {
        let d = tempfile::tempdir().unwrap();
        upsert(d.path(), project("aa", 1)).unwrap();
        upsert(d.path(), project("bb", 5)).unwrap();
        upsert(d.path(), project("aa", 9)).unwrap();
        let ids: Vec<_> = load_projects(d.path()).unwrap().into_iter().map(|p| (p.id, p.last_used)).collect();
        assert_eq!(ids, vec![("aa".to_string(), 9), ("bb".to_string(), 5)]);
        remove(d.path(), "aa").unwrap();
        assert_eq!(load_projects(d.path()).unwrap().len(), 1);
        assert!(find(d.path(), "zz").is_err());
    }

    #[test]
    fn slug() {
        for ok in ["ab", "a-1", "0x"] {
            assert!(valid_slug(ok), "{ok}");
        }
        for bad in ["a", "-a", "A1", "a_b", "a b", "a\n", &"a".repeat(64)] {
            assert!(!valid_slug(bad), "{bad}");
        }
    }

    #[test]
    fn urls() {
        assert_eq!(valid_url("http://127.0.0.1:8080/").unwrap(), ("http://127.0.0.1:8080".into(), None));
        assert!(valid_url("http://sandman.example").unwrap().1.is_some());
        assert!(valid_url("https://sandman.example/x").unwrap().1.is_none());
        for bad in ["ftp://x.example", "file:///etc", "https://u:p@x.example", "https://x.example/?a=1", "nope"] {
            assert!(valid_url(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn checkout_needs_compose_file() {
        let d = tempfile::tempdir().unwrap();
        assert!(valid_checkout(d.path().to_str().unwrap()).is_err());
        fs::create_dir(d.path().join("deploy")).unwrap();
        fs::write(d.path().join("deploy/docker-compose.yml"), "").unwrap();
        assert!(valid_checkout(d.path().to_str().unwrap()).is_ok());
    }

    #[test]
    fn app_settings_defaults_and_round_trip() {
        let d = tempfile::tempdir().unwrap();
        let def = load_app_settings(d.path());
        assert_eq!(def, AppSettings::default());
        let set = AppSettings {
            language: Language::De,
            theme: Theme::Dark,
            font_size: FontSize::Large,
        };
        save_app_settings(d.path(), &set).unwrap();
        assert_eq!(load_app_settings(d.path()), set);
        let raw = fs::read_to_string(d.path().join("settings.json")).unwrap();
        assert!(raw.contains("\"fontSize\": \"large\""), "{raw}");
    }

    #[test]
    fn app_settings_tolerate_bad_files() {
        let d = tempfile::tempdir().unwrap();
        let write = |s: &str| fs::write(d.path().join("settings.json"), s).unwrap();
        write("{not json");
        assert_eq!(load_app_settings(d.path()), AppSettings::default());
        write(r#"{"language":"de","extra":1,"theme":"dark"}"#);
        let s = load_app_settings(d.path());
        assert_eq!(s.language, Language::De);
        assert_eq!(s.theme, Theme::Dark);
        assert_eq!(s.font_size, FontSize::Normal);
        write(r#"{"language":"fr","theme":"dark","fontSize":7}"#);
        let s = load_app_settings(d.path());
        assert_eq!(s.language, Language::System);
        assert_eq!(s.theme, Theme::Dark);
        assert_eq!(s.font_size, FontSize::Normal);
    }
}
