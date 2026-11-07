use serde_json::Value;
use sha2::{Digest, Sha256};
use unicode_normalization::UnicodeNormalization;

pub const SEP: &str = "\x1f";
const WS: [char; 6] = [' ', '\t', '\n', '\r', '\x0c', '\x0b'];

pub fn sha256_hex(data: impl AsRef<[u8]>) -> String {
    Sha256::digest(data.as_ref())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

pub fn stable_id(prefix: &str, parts: &[&str]) -> String {
    format!("{prefix}_{}", &sha256_hex(parts.join(SEP))[..32])
}

pub fn canonical_json(value: &Value) -> String {
    serde_json_canonicalizer::to_string(value).expect("a serde_json Value always has a JSON form")
}

fn collapse_ws(text: &str) -> String {
    text.split(|c| WS.contains(&c))
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

fn slug(heading: &str) -> String {
    let mut out = String::new();
    let mut dash = false;
    for c in heading.nfc().map(|c| c.to_ascii_lowercase()) {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            if dash && !out.is_empty() {
                out.push('-');
            }
            dash = false;
            out.push(c);
        } else {
            dash = true;
        }
    }
    if out.is_empty() {
        "section".into()
    } else {
        out
    }
}

pub fn section_local_key(headings: &[&str], occurrences: &[u32]) -> Result<String, String> {
    if headings.len() != occurrences.len() {
        return Err("headings and occurrences differ in length".into());
    }
    let parts: Vec<String> = headings
        .iter()
        .zip(occurrences)
        .map(|(h, n)| {
            if *n >= 2 {
                format!("{}~{n}", slug(h))
            } else {
                slug(h)
            }
        })
        .collect();
    Ok(format!("sec:{}", parts.join("/")))
}

pub fn sentence_local_key(section_key: &str, text: &str, occurrence: u32) -> String {
    let normalized = collapse_ws(&text.nfc().collect::<String>());
    let suffix = if occurrence >= 2 {
        format!("~{occurrence}")
    } else {
        String::new()
    };
    format!("{section_key}/{}{suffix}", &sha256_hex(normalized)[..12])
}

/// NFC, Unicode full case folding (`caseless`), NFC, ASCII whitespace collapsed.
pub fn normalize_concept_name(name: &str) -> String {
    let nfc: String = name.nfc().collect();
    let folded: String = caseless::default_case_fold_str(&nfc).nfc().collect();
    collapse_ws(&folded)
}

pub fn fingerprint<S: AsRef<str>>(kind: &str, detector_id: &str, involved: &[S]) -> String {
    let mut keys: Vec<&str> = involved.iter().map(|s| s.as_ref()).collect();
    keys.sort_unstable_by(|a, b| a.as_bytes().cmp(b.as_bytes()));
    let mut all = vec![kind, detector_id];
    all.extend(keys);
    sha256_hex(all.join(SEP))
}

pub fn evidence_hash<S: AsRef<str>>(pairs: &[S]) -> String {
    let mut unique: Vec<&str> = pairs.iter().map(|s| s.as_ref()).collect();
    unique.sort_unstable_by(|a, b| a.as_bytes().cmp(b.as_bytes()));
    unique.dedup();
    sha256_hex(unique.join(SEP))
}

pub fn new_run_id() -> String {
    format!("run_{}", uuid::Uuid::new_v4().simple())
}

pub fn new_workflow_id() -> String {
    format!("wf_{}", uuid::Uuid::new_v4().simple())
}
