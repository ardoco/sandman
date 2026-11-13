use std::fmt::Display;

const SEP: char = '\u{1f}';

pub fn coded(code: &str, params: serde_json::Value, text: impl Display) -> String {
    format!("{SEP}{code}{SEP}{params}{SEP}{text}")
}

pub fn plain(s: &str) -> &str {
    let mut parts = s.splitn(4, SEP);
    match (parts.next(), parts.next(), parts.next(), parts.next()) {
        (Some(""), Some(_), Some(_), Some(text)) => text,
        _ => s,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn coded_text_keeps_the_english_fallback() {
        let s = coded("port_in_use", json!({"port": 8080}), "Port 8080 is in use.");
        assert_eq!(plain(&s), "Port 8080 is in use.");
        assert_eq!(plain("no code"), "no code");
        assert!(s.contains("port_in_use") && s.contains("8080"));
    }
}
