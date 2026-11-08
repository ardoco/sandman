use crate::{env, store, AppState};
use serde::{Deserialize, Serialize};
use std::{fs, path::Path};
use tauri::State;

pub const KINDS: [&str; 4] = ["OPENAI_API_KEY", "OPENROUTER_API_KEY", "SELFHOSTED_LLM_KEY", "SELFHOSTED_LLM_URL"];
const URL_KIND: &str = "SELFHOSTED_LLM_URL";
const MAX_LABEL: usize = 100;
const MAX_VALUE: usize = 512;
const FILE: &str = "keystore.json";

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct KeyEntry {
    pub id: String,
    pub label: String,
    pub kind: String,
    pub created_at: u64,
    pub last_used: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    value: Option<String>,
}

#[derive(Serialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct KeyInfo {
    pub id: String,
    pub label: String,
    pub kind: String,
    pub created_at: u64,
    pub last_used: u64,
    pub hint: String,
}

pub trait Vault {
    fn get(&self, account: &str) -> Result<Option<String>, String>;
    fn set(&self, account: &str, value: &str) -> Result<(), String>;
    fn delete(&self, account: &str) -> Result<(), String>;
}

pub struct Keychain;

impl Vault for Keychain {
    fn get(&self, account: &str) -> Result<Option<String>, String> {
        store::secret_get(account)
    }
    fn set(&self, account: &str, value: &str) -> Result<(), String> {
        store::secret_set(account, value)
    }
    fn delete(&self, account: &str) -> Result<(), String> {
        store::secret_delete(account)
    }
}

fn account(id: &str) -> String {
    format!("key:{id}")
}

fn load(dir: &Path) -> Result<Vec<KeyEntry>, String> {
    match fs::read_to_string(dir.join(FILE)) {
        Ok(s) => serde_json::from_str(&s).map_err(|e| format!("{FILE}: {e}")),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(vec![]),
        Err(e) => Err(e.to_string()),
    }
}

fn write(dir: &Path, list: &[KeyEntry]) -> Result<(), String> {
    store::write_json(dir, FILE, &list)
}

fn value_of(v: &dyn Vault, e: &KeyEntry) -> Result<Option<String>, String> {
    if e.kind == URL_KIND {
        Ok(e.value.clone())
    } else {
        v.get(&account(&e.id))
    }
}

pub fn hint(value: &str) -> String {
    let n = value.chars().count();
    if n >= 12 {
        value.chars().skip(n - 4).collect()
    } else {
        String::new()
    }
}

fn info(v: &dyn Vault, e: &KeyEntry) -> KeyInfo {
    let value = value_of(v, e).ok().flatten().unwrap_or_default();
    let hint = if e.kind == URL_KIND { value } else { hint(&value) };
    KeyInfo { id: e.id.clone(), label: e.label.clone(), kind: e.kind.clone(), created_at: e.created_at, last_used: e.last_used, hint }
}

pub fn valid_label(label: &str) -> Result<String, String> {
    let l = label.trim();
    if l.is_empty() || l.chars().count() > MAX_LABEL || l.chars().any(char::is_control) {
        return Err("label must have 1 to 100 characters".into());
    }
    Ok(l.to_string())
}

pub fn valid_entry(kind: &str, label: &str, value: &str) -> Result<(String, String), String> {
    if !KINDS.contains(&kind) {
        return Err(format!("unknown key kind {kind}"));
    }
    let label = valid_label(label)?;
    let value = value.trim();
    if value.is_empty() || value.len() > MAX_VALUE {
        return Err("value must have 1 to 512 characters".into());
    }
    env::valid_value(value)?;
    let value = if kind == URL_KIND { store::valid_url(value)?.0 } else { value.to_string() };
    Ok((label, value))
}

pub fn list(dir: &Path, v: &dyn Vault) -> Result<Vec<KeyInfo>, String> {
    Ok(load(dir)?.iter().map(|e| info(v, e)).collect())
}

pub fn save(dir: &Path, v: &dyn Vault, kind: &str, label: &str, value: &str) -> Result<KeyInfo, String> {
    let (label, value) = valid_entry(kind, label, value)?;
    let mut list = load(dir)?;
    let now = store::now();
    let mut same = None;
    for (i, e) in list.iter().enumerate().filter(|(_, e)| e.kind == kind) {
        if value_of(v, e)?.as_deref() == Some(value.as_str()) {
            same = Some(i);
            break;
        }
    }
    let i = match same {
        Some(i) => {
            list[i].label = label;
            list[i].last_used = now;
            i
        }
        None => {
            let mut n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
            while list.iter().any(|e| e.id == format!("{n:x}")) {
                n += 1;
            }
            let id = format!("{n:x}");
            let url = kind == URL_KIND;
            if !url {
                v.set(&account(&id), &value)?;
            }
            list.push(KeyEntry { id, label, kind: kind.into(), created_at: now, last_used: now, value: url.then(|| value.clone()) });
            list.len() - 1
        }
    };
    write(dir, &list)?;
    Ok(info(v, &list[i]))
}

pub fn get_value(dir: &Path, v: &dyn Vault, id: &str) -> Result<String, String> {
    let mut list = load(dir)?;
    let e = list.iter_mut().find(|e| e.id == id).ok_or("unknown key")?;
    let value = value_of(v, e)?.ok_or("the key value is missing from the keychain")?;
    e.last_used = store::now();
    write(dir, &list)?;
    Ok(value)
}

pub fn rename(dir: &Path, id: &str, label: &str) -> Result<(), String> {
    let label = valid_label(label)?;
    let mut list = load(dir)?;
    list.iter_mut().find(|e| e.id == id).ok_or("unknown key")?.label = label;
    write(dir, &list)
}

pub fn delete(dir: &Path, v: &dyn Vault, id: &str) -> Result<(), String> {
    let mut list = load(dir)?;
    if !list.iter().any(|e| e.id == id) {
        return Err("unknown key".into());
    }
    v.delete(&account(id))?;
    list.retain(|e| e.id != id);
    write(dir, &list)
}

#[tauri::command]
pub fn list_keys(state: State<AppState>) -> Result<Vec<KeyInfo>, String> {
    list(&state.dir, &Keychain)
}

#[tauri::command]
pub fn save_key(state: State<AppState>, kind: String, label: String, value: String) -> Result<KeyInfo, String> {
    save(&state.dir, &Keychain, &kind, &label, &value)
}

#[tauri::command]
pub fn get_key_value(state: State<AppState>, id: String) -> Result<String, String> {
    get_value(&state.dir, &Keychain, &id)
}

#[tauri::command]
pub fn rename_key(state: State<AppState>, id: String, label: String) -> Result<(), String> {
    rename(&state.dir, &id, &label)
}

#[tauri::command]
pub fn delete_key(state: State<AppState>, id: String) -> Result<(), String> {
    delete(&state.dir, &Keychain, &id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{collections::HashMap, sync::Mutex};

    #[derive(Default)]
    struct Mem(Mutex<HashMap<String, String>>);
    impl Vault for Mem {
        fn get(&self, a: &str) -> Result<Option<String>, String> {
            Ok(self.0.lock().unwrap().get(a).cloned())
        }
        fn set(&self, a: &str, v: &str) -> Result<(), String> {
            self.0.lock().unwrap().insert(a.into(), v.into());
            Ok(())
        }
        fn delete(&self, a: &str) -> Result<(), String> {
            self.0.lock().unwrap().remove(a);
            Ok(())
        }
    }

    const KEY: &str = "sk-abcdefghijklmnop1234";

    #[test]
    fn round_trip_keeps_secrets_out_of_json() {
        let d = tempfile::tempdir().unwrap();
        let v = Mem::default();
        let k = save(d.path(), &v, "OPENAI_API_KEY", "Work", KEY).unwrap();
        assert_eq!(k.hint, "1234");
        assert_eq!(v.get(&format!("key:{}", k.id)).unwrap().as_deref(), Some(KEY));
        let json = fs::read_to_string(d.path().join(FILE)).unwrap();
        assert!(!json.contains("abcdefgh") && json.contains("Work"));
        let listed = list(d.path(), &v).unwrap();
        assert_eq!(listed, vec![k.clone()]);
        assert_eq!(get_value(d.path(), &v, &k.id).unwrap(), KEY);
        rename(d.path(), &k.id, "Home").unwrap();
        assert_eq!(list(d.path(), &v).unwrap()[0].label, "Home");
        delete(d.path(), &v, &k.id).unwrap();
        assert!(list(d.path(), &v).unwrap().is_empty());
        assert_eq!(v.get(&format!("key:{}", k.id)).unwrap(), None);
        assert!(get_value(d.path(), &v, &k.id).is_err());
        assert!(delete(d.path(), &v, &k.id).is_err());
    }

    #[test]
    fn dedupes_same_kind_and_value_only() {
        let d = tempfile::tempdir().unwrap();
        let v = Mem::default();
        let a = save(d.path(), &v, "OPENAI_API_KEY", "A", KEY).unwrap();
        let b = save(d.path(), &v, "OPENAI_API_KEY", "B", KEY).unwrap();
        assert_eq!(a.id, b.id);
        assert_eq!(b.label, "B");
        let c = save(d.path(), &v, "OPENROUTER_API_KEY", "C", KEY).unwrap();
        let e = save(d.path(), &v, "OPENAI_API_KEY", "E", "sk-other-other-other").unwrap();
        assert_eq!(list(d.path(), &v).unwrap().len(), 3);
        assert!(c.id != a.id && e.id != a.id);
    }

    #[test]
    fn url_values_live_in_json_not_keychain() {
        let d = tempfile::tempdir().unwrap();
        let v = Mem::default();
        let k = save(d.path(), &v, "SELFHOSTED_LLM_URL", "Lab", "https://llm.example/ollama/").unwrap();
        assert_eq!(k.hint, "https://llm.example/ollama");
        assert!(v.0.lock().unwrap().is_empty());
        let again = save(d.path(), &v, "SELFHOSTED_LLM_URL", "Lab2", "https://llm.example/ollama").unwrap();
        assert_eq!(again.id, k.id);
    }

    #[test]
    fn validates_input() {
        fn ok(k: &str, l: &str, v: &str) -> bool {
            valid_entry(k, l, v).is_ok()
        }
        assert!(ok("OPENAI_API_KEY", "x", "sk-1"));
        assert!(!ok("OTHER", "x", "sk-1"));
        assert!(!ok("OPENAI_API_KEY", "  ", "sk-1"));
        assert!(!ok("OPENAI_API_KEY", "a\nb", "sk-1"));
        assert!(!ok("OPENAI_API_KEY", &"a".repeat(101), "sk-1"));
        assert!(!ok("OPENAI_API_KEY", "x", " "));
        assert!(!ok("OPENAI_API_KEY", "x", &"a".repeat(513)));
        assert!(!ok("OPENAI_API_KEY", "x", "a b"));
        assert!(!ok("SELFHOSTED_LLM_URL", "x", "file:///etc"));
        assert!(rename(tempfile::tempdir().unwrap().path(), "zz", " ").is_err());
    }

    #[test]
    fn hint_hides_short_values() {
        assert_eq!(hint("short"), "");
        assert_eq!(hint("abcdefgh1234"), "1234");
    }
}
