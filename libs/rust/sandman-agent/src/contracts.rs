use std::collections::HashMap;
use std::path::Path;
use std::sync::Mutex;

use jsonschema::{Registry, Validator};
use serde_json::{Value, json};

const SCHEMA_FILES: [&str; 4] = [
    "common.schema.json",
    "skills.schema.json",
    "events.schema.json",
    "knowledge.schema.json",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContractError {
    pub reference: String,
    pub messages: Vec<String>,
}

impl std::fmt::Display for ContractError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.reference, self.messages.join("; "))
    }
}
impl std::error::Error for ContractError {}

/// JSON Schema 2020-12 validation against `contracts/*.schema.json`. No network access.
/// Format keywords are not enforced (parity with the Python and TypeScript kits).
pub struct Contracts {
    schemas: Vec<(String, Value)>,
    base: HashMap<String, String>,
    validators: Mutex<HashMap<String, Validator>>,
}

impl Contracts {
    pub fn new(contracts_dir: &Path) -> Result<Self, String> {
        let mut names: Vec<String> = SCHEMA_FILES.iter().map(|s| s.to_string()).collect();
        let mut config: Vec<String> = std::fs::read_dir(contracts_dir.join("config"))
            .map_err(|e| format!("cannot read contracts config dir: {e}"))?
            .filter_map(|e| e.ok()?.file_name().into_string().ok())
            .filter(|f| f.ends_with(".schema.json"))
            .map(|f| format!("config/{f}"))
            .collect();
        config.sort();
        names.extend(config);
        let (mut schemas, mut base) = (Vec::new(), HashMap::new());
        for name in names {
            let text = std::fs::read_to_string(contracts_dir.join(&name))
                .map_err(|e| format!("{name}: {e}"))?;
            let schema: Value = serde_json::from_str(&text).map_err(|e| format!("{name}: {e}"))?;
            let id = schema["$id"]
                .as_str()
                .ok_or_else(|| format!("{name}: $id missing"))?
                .to_string();
            base.insert(name, id.clone());
            schemas.push((id, schema));
        }
        let contracts = Self {
            schemas,
            base,
            validators: Mutex::new(HashMap::new()),
        };
        contracts.registry()?;
        Ok(contracts)
    }

    fn registry(&self) -> Result<Registry<'static>, String> {
        let mut builder = Registry::new();
        for (id, schema) in &self.schemas {
            builder = builder.add(id, schema.clone()).map_err(|e| e.to_string())?;
        }
        builder.prepare().map_err(|e| e.to_string())
    }

    fn uri(&self, reference: &str) -> String {
        let (file, fragment) = reference
            .split_once('#')
            .map_or((reference, ""), |(f, g)| (f, g));
        let base = self.base.get(file).map_or(file, String::as_str);
        if reference.contains('#') {
            format!("{base}#{fragment}")
        } else {
            base.to_string()
        }
    }

    pub fn validate(&self, reference: &str, instance: &Value) -> Result<(), ContractError> {
        let uri = self.uri(reference);
        let fail = |m: String| ContractError {
            reference: reference.into(),
            messages: vec![m],
        };
        let mut cache = self.validators.lock().unwrap();
        if !cache.contains_key(&uri) {
            let registry = self.registry().map_err(fail)?;
            let validator = jsonschema::options()
                .should_validate_formats(false)
                .with_registry(&registry)
                .build(&json!({ "$ref": uri }))
                .map_err(|e| fail(e.to_string()))?;
            cache.insert(uri.clone(), validator);
        }
        let errors: Vec<String> = cache[&uri]
            .iter_errors(instance)
            .map(|e| {
                let path = e.instance_path().to_string();
                format!("{}: {e}", if path.is_empty() { "<root>" } else { &path })
            })
            .collect();
        if errors.is_empty() {
            Ok(())
        } else {
            Err(ContractError {
                reference: reference.into(),
                messages: errors,
            })
        }
    }

    pub fn is_valid(&self, reference: &str, instance: &Value) -> bool {
        self.validate(reference, instance).is_ok()
    }
}
