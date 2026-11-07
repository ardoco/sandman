use std::collections::HashMap;
use std::path::PathBuf;

pub const LLM_MODES: [&str; 3] = ["live", "record", "replay"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigError(pub String);

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for ConfigError {}

pub type Env = HashMap<String, String>;

pub fn process_env() -> Env {
    std::env::vars().collect()
}

pub fn default_contracts_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../contracts")
}

pub fn llm_mode(env: &Env) -> Result<String, ConfigError> {
    let mode = env
        .get("SANDMAN_LLM_MODE")
        .filter(|m| !m.is_empty())
        .map_or("live", String::as_str);
    if LLM_MODES.contains(&mode) {
        Ok(mode.to_string())
    } else {
        Err(ConfigError(format!(
            "invalid SANDMAN_LLM_MODE '{mode}', expected one of {}",
            LLM_MODES.join(", ")
        )))
    }
}

#[derive(Debug, Clone, Default)]
pub struct Settings {
    pub service_name: String,
    pub contracts_dir: PathBuf,
    pub llm_base_url: Option<String>,
    pub llm_key: Option<String>,
    pub llm_local_key: Option<String>,
    pub kg_uri: Option<String>,
    pub kg_user: Option<String>,
    pub kg_password: Option<String>,
    pub nats_url: Option<String>,
    pub service_token: Option<String>,
    pub service_verify: HashMap<String, String>,
    pub plugin_inbound_token: Option<String>,
}

fn read_file(name: &str, path: &str) -> Result<String, ConfigError> {
    std::fs::read_to_string(path)
        .map_err(|e| ConfigError(format!("cannot read {name}={path}: {e}")))
}

pub fn settings_from_env(env: &Env) -> Result<Settings, ConfigError> {
    let plain = |name: &str| env.get(name).filter(|v| !v.is_empty()).cloned();
    let secret = |name: &str| -> Result<Option<String>, ConfigError> {
        let value = match env.get(&format!("{name}_FILE")) {
            Some(file) => Some(read_file(&format!("{name}_FILE"), file)?.trim().to_string()),
            None => env.get(name).cloned(),
        };
        Ok(value.filter(|v| !v.is_empty()))
    };
    let service_name = plain("SANDMAN_SERVICE_NAME")
        .ok_or_else(|| ConfigError("SANDMAN_SERVICE_NAME is required".into()))?;
    let service_verify = match plain("SANDMAN_SERVICE_VERIFY_FILE") {
        Some(file) => serde_json::from_str(&read_file("SANDMAN_SERVICE_VERIFY_FILE", &file)?)
            .map_err(|e| {
                ConfigError(format!(
                    "cannot read SANDMAN_SERVICE_VERIFY_FILE={file}: {e}"
                ))
            })?,
        None => HashMap::new(),
    };
    Ok(Settings {
        service_name,
        contracts_dir: plain("SANDMAN_CONTRACTS_DIR")
            .map_or_else(default_contracts_dir, PathBuf::from),
        llm_base_url: plain("SANDMAN_LLM_BASE_URL"),
        llm_key: secret("SANDMAN_LLM_KEY")?,
        llm_local_key: secret("SANDMAN_LLM_LOCAL_KEY")?,
        kg_uri: plain("SANDMAN_KG_URI"),
        kg_user: plain("SANDMAN_KG_USER"),
        kg_password: secret("SANDMAN_KG_PASSWORD")?,
        nats_url: plain("SANDMAN_NATS_URL"),
        service_token: secret("SANDMAN_SERVICE_TOKEN")?,
        service_verify,
        plugin_inbound_token: secret("SANDMAN_PLUGIN_INBOUND_TOKEN")?,
    })
}

impl Settings {
    /// Names are the `SANDMAN_*` variable names, for example `SANDMAN_KG_URI`.
    pub fn require(&self, names: &[&str]) -> Result<(), ConfigError> {
        let missing: Vec<&str> = names
            .iter()
            .copied()
            .filter(|n| match *n {
                "SANDMAN_LLM_BASE_URL" => self.llm_base_url.is_none(),
                "SANDMAN_LLM_KEY" => self.llm_key.is_none(),
                "SANDMAN_LLM_LOCAL_KEY" => self.llm_local_key.is_none(),
                "SANDMAN_KG_URI" => self.kg_uri.is_none(),
                "SANDMAN_KG_USER" => self.kg_user.is_none(),
                "SANDMAN_KG_PASSWORD" => self.kg_password.is_none(),
                "SANDMAN_NATS_URL" => self.nats_url.is_none(),
                "SANDMAN_SERVICE_TOKEN" => self.service_token.is_none(),
                "SANDMAN_SERVICE_VERIFY" => self.service_verify.is_empty(),
                "SANDMAN_PLUGIN_INBOUND_TOKEN" => self.plugin_inbound_token.is_none(),
                _ => true,
            })
            .collect();
        if missing.is_empty() {
            Ok(())
        } else {
            Err(ConfigError(format!(
                "{}: missing configuration {}",
                self.service_name,
                missing.join(", ")
            )))
        }
    }
}
