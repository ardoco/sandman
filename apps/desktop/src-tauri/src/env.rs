use crate::store::{secret_get, valid_checkout, Defaults};
use serde::{Deserialize, Serialize};
use std::{fs, path::Path};

#[derive(Deserialize, Default, Debug)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    pub openai_api_key: Option<String>,
    pub openrouter_api_key: Option<String>,
    pub selfhosted_llm_url: Option<String>,
    pub selfhosted_llm_key: Option<String>,
    pub corenlp_mode: Option<String>,
}

#[derive(Serialize, Default, Debug)]
#[serde(rename_all = "camelCase")]
pub struct SettingsView {
    pub selfhosted_llm_url: String,
    pub corenlp_mode: String,
    pub keys_set: Vec<&'static str>,
}

pub const SECRET_FIELDS: [(&str, &str); 3] =
    [("openaiApiKey", "OPENAI_API_KEY"), ("openrouterApiKey", "OPENROUTER_API_KEY"), ("selfhostedLlmKey", "SELFHOSTED_LLM_KEY")];

impl Settings {
    pub fn secret(&self, field: &str) -> Option<&str> {
        match field {
            "openaiApiKey" => &self.openai_api_key,
            "openrouterApiKey" => &self.openrouter_api_key,
            _ => &self.selfhosted_llm_key,
        }
        .as_deref()
        .filter(|v| !v.is_empty())
    }
}

pub fn valid_value(v: &str) -> Result<(), String> {
    if v.chars().all(|c| c.is_ascii_alphanumeric() || "_.:/@+=~%?&,-".contains(c)) {
        Ok(())
    } else {
        Err("value contains a character that is not allowed in an env file (no spaces, quotes, # or line breaks)".into())
    }
}

pub fn get_var(text: &str, key: &str) -> Option<String> {
    text.lines().find_map(|l| l.strip_prefix(key)?.strip_prefix('=')).map(str::to_string)
}

pub fn set_var(text: &str, key: &str, value: &str) -> String {
    let mut out = String::new();
    let mut done = false;
    for line in text.split_inclusive('\n') {
        if !done && line.strip_prefix(key).is_some_and(|r| r.starts_with('=')) {
            out.push_str(&format!("{key}={value}"));
            out.push_str(if line.ends_with('\n') { "\n" } else { "" });
            done = true;
        } else {
            out.push_str(line);
        }
    }
    if !done {
        if !out.is_empty() && !out.ends_with('\n') {
            out.push('\n');
        }
        out.push_str(&format!("{key}={value}\n"));
    }
    out
}

pub fn apply(gateway: &str, dotenv: &str, s: &Settings) -> Result<(String, String), String> {
    let mut g = gateway.to_string();
    for (field, var) in SECRET_FIELDS {
        if let Some(v) = s.secret(field) {
            valid_value(v)?;
            g = set_var(&g, var, v);
        }
    }
    if let Some(u) = s.selfhosted_llm_url.as_deref().filter(|v| !v.is_empty()) {
        valid_value(u)?;
        crate::store::valid_url(u)?;
        g = set_var(&g, "SELFHOSTED_LLM_URL", u);
    }
    let mut d = dotenv.to_string();
    if let Some(m) = s.corenlp_mode.as_deref().filter(|v| !v.is_empty()) {
        if !matches!(m, "local" | "remote") {
            return Err("CoreNLP mode must be local or remote".into());
        }
        d = set_var(&d, "SANDMAN_CORENLP_MODE", m);
    }
    Ok((g, d))
}

pub fn view(gateway: &str, dotenv: &str) -> SettingsView {
    SettingsView {
        selfhosted_llm_url: get_var(gateway, "SELFHOSTED_LLM_URL").unwrap_or_default(),
        corenlp_mode: get_var(dotenv, "SANDMAN_CORENLP_MODE").unwrap_or_else(|| "remote".into()),
        keys_set: SECRET_FIELDS
            .iter()
            .filter(|(_, var)| get_var(gateway, var).is_some_and(|v| !v.is_empty()))
            .map(|(f, _)| *f)
            .collect(),
    }
}

pub fn default_view(d: &Defaults) -> Result<SettingsView, String> {
    let mut keys_set = vec![];
    for (field, _) in SECRET_FIELDS {
        if secret_get(&format!("default:{field}"))?.is_some() {
            keys_set.push(field);
        }
    }
    Ok(SettingsView { selfhosted_llm_url: d.selfhosted_llm_url.clone(), corenlp_mode: d.corenlp_mode.clone(), keys_set })
}

pub fn paths(checkout: &str) -> Result<(std::path::PathBuf, std::path::PathBuf, std::path::PathBuf), String> {
    let c = valid_checkout(checkout)?;
    Ok((c.join("deploy/secrets/gateway.env"), c.join("deploy/.env"), c.join("deploy/.env.example")))
}

pub fn read_text(p: &Path) -> String {
    fs::read_to_string(p).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    const GATEWAY: &str = "OPENAI_API_KEY=\nOPENROUTER_API_KEY=old\nSELFHOSTED_LLM_URL=http://ollama:11434\nBIFROST_ENCRYPTION_KEY=abc\n";
    const DOTENV: &str = "# keep me\nTRAVERSE_USER=sandman\n\n# CoreNLP\nSANDMAN_CORENLP_MODE=remote\nOTEL_SDK_DISABLED=true";

    #[test]
    fn keeps_unrelated_lines_and_comments() {
        let s = Settings {
            openai_api_key: Some("sk-new".into()),
            selfhosted_llm_url: Some("https://llm.example/ollama".into()),
            corenlp_mode: Some("local".into()),
            ..Default::default()
        };
        let (g, d) = apply(GATEWAY, DOTENV, &s).unwrap();
        assert_eq!(
            g,
            "OPENAI_API_KEY=sk-new\nOPENROUTER_API_KEY=old\nSELFHOSTED_LLM_URL=https://llm.example/ollama\nBIFROST_ENCRYPTION_KEY=abc\n"
        );
        assert_eq!(d, DOTENV.replace("MODE=remote", "MODE=local"));
    }

    #[test]
    fn appends_missing_key_and_no_overwrite_without_value() {
        assert_eq!(set_var("A=1", "B", "2"), "A=1\nB=2\n");
        let (g, d) = apply(GATEWAY, DOTENV, &Settings { openai_api_key: Some(String::new()), ..Default::default() }).unwrap();
        assert_eq!((g.as_str(), d.as_str()), (GATEWAY, DOTENV));
    }

    #[test]
    fn rejects_injection() {
        for bad in ["a\nB=1", "a b", "\"x\"", "x'y", "a#b", "a\r", "$(x)", "a\\b"] {
            let s = Settings { openai_api_key: Some(bad.into()), ..Default::default() };
            assert!(apply(GATEWAY, DOTENV, &s).is_err(), "{bad:?}");
        }
        let s = Settings { corenlp_mode: Some("x\nA=1".into()), ..Default::default() };
        assert!(apply(GATEWAY, DOTENV, &s).is_err());
        let s = Settings { selfhosted_llm_url: Some("file:///x".into()), ..Default::default() };
        assert!(apply(GATEWAY, DOTENV, &s).is_err());
    }

    #[test]
    fn view_reports_set_keys_without_values() {
        let v = view(GATEWAY, DOTENV);
        assert_eq!(v.keys_set, vec!["openrouterApiKey"]);
        assert_eq!(v.corenlp_mode, "remote");
    }
}
