use std::collections::HashMap;
use std::sync::Arc;

use serde_json::{Map, Value};
use subtle::ConstantTimeEq;

use crate::ids::sha256_hex;

pub const QUERY_SERVICE: &str = "query-service";
pub const ORCHESTRATOR: &str = "orchestrator";
pub const PLUGIN_PREFIX: &str = "plugin:";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ForbiddenError(pub String);

impl std::fmt::Display for ForbiddenError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for ForbiddenError {}

fn ct_eq(a: &str, b: &str) -> bool {
    a.len() == b.len() && bool::from(a.as_bytes().ct_eq(b.as_bytes()))
}

pub struct TokenVerifier {
    hashes: Vec<(String, String)>,
}

impl TokenVerifier {
    pub fn new(hashes: &HashMap<String, String>) -> Self {
        let mut hashes: Vec<_> = hashes.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        hashes.sort();
        Self { hashes }
    }

    pub fn caller(&self, token: &str) -> Option<String> {
        if token.is_empty() {
            return None;
        }
        let digest = sha256_hex(token);
        let mut found = None;
        for (name, expected) in &self.hashes {
            if ct_eq(&digest, expected) {
                found = Some(name.clone());
            }
        }
        found
    }

    pub fn caller_from_header(&self, authorization: Option<&str>) -> Option<String> {
        bearer_token(authorization).and_then(|t| self.caller(t))
    }
}

pub fn bearer_token(authorization: Option<&str>) -> Option<&str> {
    let value = authorization?;
    if value.len() < 7 || !value.is_char_boundary(7) || !value[..7].eq_ignore_ascii_case("bearer ")
    {
        return None;
    }
    let token = value[7..].trim();
    (!token.is_empty()).then_some(token)
}

pub fn actor_from_metadata(
    caller: &str,
    metadata: &Map<String, Value>,
) -> Result<Option<String>, ForbiddenError> {
    match metadata.get("sandman.actor") {
        None | Some(Value::Null) => Ok(None),
        Some(actor) => {
            if caller != ORCHESTRATOR {
                return Err(ForbiddenError(
                    "sandman.actor is accepted only from the orchestrator".into(),
                ));
            }
            Ok(actor.as_str().map(str::to_string))
        }
    }
}

/// Maps an Authorization header to a caller name, or `None` when the token is invalid.
pub type Authenticator = Arc<dyn Fn(Option<&str>) -> Option<String> + Send + Sync>;

pub fn authenticator_for(
    plugin_inbound_token: Option<&str>,
    service_verify: &HashMap<String, String>,
) -> Authenticator {
    if let Some(inbound) = plugin_inbound_token.filter(|t| !t.is_empty()) {
        let want = sha256_hex(inbound);
        return Arc::new(move |authorization| {
            let token = bearer_token(authorization)?;
            ct_eq(&sha256_hex(token), &want).then(|| "owner".to_string())
        });
    }
    let verifier = TokenVerifier::new(service_verify);
    Arc::new(move |authorization| {
        verifier
            .caller_from_header(authorization)
            .filter(|c| !c.starts_with(PLUGIN_PREFIX))
    })
}
