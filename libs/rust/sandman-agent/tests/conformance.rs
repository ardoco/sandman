use std::collections::BTreeSet;

use sandman_agent::config::default_contracts_dir;
use sandman_agent::contracts::Contracts;
use sandman_agent::events::{event_from_json, event_to_json};
use sandman_agent::ids::{
    canonical_json, evidence_hash, fingerprint, normalize_concept_name, section_local_key,
    sentence_local_key, sha256_hex, stable_id,
};
use serde_json::Value;

fn vectors() -> Value {
    let path = default_contracts_dir().join("conformance/vectors.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn cases(group: &str) -> Vec<Value> {
    vectors()[group]["cases"].as_array().unwrap().clone()
}

fn s(v: &Value) -> &str {
    v.as_str().unwrap()
}

fn strs(v: &Value) -> Vec<&str> {
    v.as_array().unwrap().iter().map(s).collect()
}

#[test]
fn stable_id_vectors() {
    for c in cases("stableId") {
        assert_eq!(
            stable_id(s(&c["prefix"]), &strs(&c["parts"])),
            s(&c["expected"])
        );
    }
}

#[test]
fn section_key_vectors() {
    for c in cases("sectionKey") {
        let occ: Vec<u32> = c["occurrences"]
            .as_array()
            .unwrap()
            .iter()
            .map(|o| o.as_u64().unwrap() as u32)
            .collect();
        assert_eq!(
            section_local_key(&strs(&c["headings"]), &occ).unwrap(),
            s(&c["expected"])
        );
    }
}

#[test]
fn sentence_key_vectors() {
    for c in cases("sentenceKey") {
        let got = sentence_local_key(
            s(&c["sectionLocalKey"]),
            s(&c["text"]),
            c["occurrence"].as_u64().unwrap() as u32,
        );
        assert_eq!(got, s(&c["expected"]));
    }
}

#[test]
fn concept_name_vectors() {
    for c in cases("conceptName") {
        assert_eq!(normalize_concept_name(s(&c["name"])), s(&c["expected"]));
    }
}

#[test]
fn fingerprint_vectors() {
    for c in cases("fingerprint") {
        let i = &c["input"];
        let fp = fingerprint(s(&i["type"]), s(&i["detector"]), &strs(&i["involved"]));
        assert_eq!(fp, s(&c["expected"]));
        if let Some(id) = c.get("expectedInconsistencyId") {
            assert_eq!(stable_id("inc", &[s(&c["projectId"]), &fp]), s(id));
        }
    }
}

#[test]
fn evidence_hash_vectors() {
    for c in cases("evidenceHash") {
        let involved = &c["involved"];
        let mut elements: Vec<&Value> = involved["elements"].as_array().unwrap().iter().collect();
        if let Some(links) = involved.get("links").and_then(Value::as_array) {
            for l in links {
                elements.extend(l["endpoints"].as_array().unwrap());
            }
        }
        let pairs: Vec<String> = elements
            .iter()
            .map(|e| format!("{}={}", s(&e["key"]), s(&e["contentHash"])))
            .collect();
        let sorted: Vec<String> = pairs
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let expected: Vec<String> = c["sortedPairs"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| s(p).to_string())
            .collect();
        assert_eq!(sorted, expected);
        assert_eq!(evidence_hash(&pairs), s(&c["expected"]));
    }
}

#[test]
fn ingestion_id_vectors() {
    for c in cases("ingestionId") {
        let i = &c["input"];
        assert_eq!(
            stable_id("ing", &[s(&i["projectId"]), &i["seq"].to_string()]),
            s(&c["expected"])
        );
    }
}

#[test]
fn trace_link_id_vectors() {
    for c in cases("traceLinkId") {
        let i = &c["input"];
        let parts = [
            "approach",
            "approachVersion",
            "configHash",
            "sourceId",
            "targetId",
        ]
        .map(|k| s(&i[k]));
        assert_eq!(stable_id("tl", &parts), s(&c["expected"]));
    }
}

#[test]
fn input_and_action_key_vectors() {
    for group in ["inputKey", "actionKey"] {
        for c in cases(group) {
            assert_eq!(canonical_json(&c["object"]), s(&c["canonical"]));
            assert_eq!(sha256_hex(s(&c["canonical"])), s(&c["expected"]));
        }
    }
}

#[test]
fn cassette_key_vectors() {
    for c in cases("cassetteKey") {
        let mut body = c["request"].clone();
        for k in ["user", "stream", "stream_options"] {
            body.as_object_mut().unwrap().remove(k);
        }
        assert_eq!(canonical_json(&body), s(&c["canonical"]));
        assert_eq!(sha256_hex(s(&c["canonical"])), s(&c["expectedKey"]));
    }
}

#[test]
fn event_envelope_vectors() {
    let contracts = Contracts::new(&default_contracts_dir()).unwrap();
    for c in cases("eventEnvelope") {
        let valid = c["valid"].as_bool().unwrap();
        assert_eq!(
            contracts.is_valid("events.schema.json", &c["event"]),
            valid,
            "{}",
            c["event"]
        );
        if valid {
            assert_eq!(
                event_to_json(&event_from_json(&c["event"]).unwrap()),
                c["event"]
            );
        }
    }
}
