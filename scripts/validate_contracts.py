"""Validates contracts/: schemas, conformance vectors, Agent Cards, OpenAPI files, KG schema.

Run: uv run --group contracts python scripts/validate_contracts.py [contracts_dir]
"""

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

import rfc8785
import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi
from openapi_spec_validator.readers import read_from_filename
from referencing import Registry, Resource

SCHEMAS = ["common.schema.json", "skills.schema.json", "events.schema.json"]
SEP = "\x1f"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def stable_id(prefix: str, parts: list[str]) -> str:
    return f"{prefix}_{sha(SEP.join(parts))[:32]}"


def slug(heading: str) -> str:
    text = "".join(c.lower() if "A" <= c <= "Z" else c for c in unicodedata.normalize("NFC", heading))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "section"


def section_key(headings: list[str], occurrences: list[int]) -> str:
    return "sec:" + "/".join(slug(h) + (f"~{n}" if n >= 2 else "") for h, n in zip(headings, occurrences, strict=True))


def sentence_key(section: str, text: str, occurrence: int) -> str:
    ws = " \t\n\r\f\v"
    normalized = re.sub(f"[{ws}]+", " ", unicodedata.normalize("NFC", text)).strip(ws)
    return f"{section}/{sha(normalized)[:12]}" + (f"~{occurrence}" if occurrence >= 2 else "")


def concept_name(name: str) -> str:
    ws = " \t\n\r\f\v"
    folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())
    return re.sub(f"[{ws}]+", " ", folded).strip(ws)


def fingerprint(type_: str, detector: str, involved: list[str]) -> str:
    return sha(SEP.join([type_, detector, *sorted(involved, key=str.encode)]))


def evidence_hash(pairs: list[str]) -> str:
    return sha(SEP.join(sorted(set(pairs), key=str.encode)))


def check(errors: list[str], label: str, ok: bool) -> None:
    if not ok:
        errors.append(label)


def main(contracts: Path) -> list[str]:
    errors: list[str] = []
    schemas = {name: json.loads((contracts / name).read_text()) for name in SCHEMAS}
    for name, schema in schemas.items():
        for e in Draft202012Validator(Draft202012Validator.META_SCHEMA).iter_errors(schema):
            errors.append(f"{name}: {e.message}")
    if errors:
        return errors
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas.values())

    def validator(ref: str) -> Draft202012Validator:
        return Draft202012Validator({"$ref": ref}, registry=registry)

    v = json.loads((contracts / "conformance/vectors.json").read_text())
    for c in v["stableId"]["cases"]:
        check(errors, f"stableId {c['parts']}", stable_id(c["prefix"], c["parts"]) == c["expected"])
    for c in v["sectionKey"]["cases"]:
        check(errors, f"sectionKey {c['headings']}", section_key(c["headings"], c["occurrences"]) == c["expected"])
    for c in v["sentenceKey"]["cases"]:
        got = sentence_key(c["sectionLocalKey"], c["text"], c["occurrence"])
        check(errors, f"sentenceKey {c['text']!r}", got == c["expected"])
    for c in v["conceptName"]["cases"]:
        check(errors, f"conceptName {c['name']!r}", concept_name(c["name"]) == c["expected"])
    for c in v["fingerprint"]["cases"]:
        i = c["input"]
        fp = fingerprint(i["type"], i["detector"], i["involved"])
        check(errors, f"fingerprint {i['type']}", fp == c["expected"])
        check(errors, f"inc id {i['type']}", stable_id("inc", [c["projectId"], fp]) == c["expectedInconsistencyId"])
    for c in v["cassetteKey"]["cases"]:
        body = {k: val for k, val in c["request"].items() if k not in ("user", "stream", "stream_options")}
        canonical = rfc8785.dumps(body).decode()
        check(errors, f"cassette canonical {c['endpoint']}", canonical == c["canonical"])
        check(errors, f"cassette key {c['endpoint']}", sha(canonical) == c["expectedKey"])
    for c in v["inputKey"]["cases"]:
        canonical = rfc8785.dumps(c["object"]).decode()
        check(errors, f"inputKey {c['name']}", canonical == c["canonical"] and sha(canonical) == c["expected"])
    for c in v["actionKey"]["cases"]:
        canonical = rfc8785.dumps(c["object"]).decode()
        check(errors, f"actionKey {c['name']}", canonical == c["canonical"] and sha(canonical) == c["expected"])
    for c in v["ingestionId"]["cases"]:
        i = c["input"]
        check(errors, f"ingestionId {i['seq']}", stable_id("ing", [i["projectId"], str(i["seq"])]) == c["expected"])
    for c in v["traceLinkId"]["cases"]:
        i = c["input"]
        parts = [i["approach"], i["approachVersion"], i["configHash"], i["sourceId"], i["targetId"]]
        check(errors, f"traceLinkId {i['approach']}", stable_id("tl", parts) == c["expected"])
        if "configSource" in c:
            canonical = rfc8785.dumps(c["configSource"]["explicitReferences"]).decode()
            check(
                errors,
                "traceLinkId explicit configHash",
                canonical == c["configCanonical"] and sha(canonical) == i["configHash"],
            )
    for c in v["evidenceHash"]["cases"]:
        inv = c["involved"]
        elements = [*inv["elements"], *(e for link in inv.get("links", []) for e in link["endpoints"])]
        pairs = [f"{e['key']}={e['contentHash']}" for e in elements]
        check(errors, f"evidenceHash {c['name']}", sorted(set(pairs), key=str.encode) == c["sortedPairs"])
        check(errors, f"evidenceHash {c['name']} hash", evidence_hash(pairs) == c["expected"])
    events = validator(schemas["events.schema.json"]["$id"])
    for c in v["eventEnvelope"]["cases"]:
        check(errors, f"eventEnvelope {c['event']['id']}", events.is_valid(c["event"]) == c["valid"])

    skill_inputs: dict[str, str] = {}
    for card_path in sorted((contracts / "agent-cards").glob("*.json")):
        card = json.loads(card_path.read_text())
        for ext in card["capabilities"]["extensions"]:
            params = ext.get("params", {})
            for skill, io in {**params.get("skills", {}), **params.get("plannedSkills", {})}.items():
                for ref in io.values():
                    base, _, pointer = ref.partition("#")
                    defs = schemas[base.rsplit("/", 1)[-1]]["$defs"]
                    check(errors, f"{card_path.name}: {ref}", pointer.rsplit("/", 1)[-1] in defs)
                if skill in params.get("skills", {}):
                    skill_inputs[skill] = io["inputSchema"]
    for c in v["skillInvocation"]["cases"]:
        if c["skill"] in skill_inputs:  # metadata/unknown-skill cases are library behavior, not schema checks
            valid = validator(skill_inputs[c["skill"]]).is_valid(c["message"]["parts"][0]["data"])
            check(errors, f"skillInvocation {c['name']}", valid == (c["expectedState"] != "TASK_STATE_REJECTED"))

    config_dir = contracts / "config"
    repo_config = contracts.parent / "config"
    config_schemas = {
        "llm/models.yaml": "llm-models",
        "orchestrator/rules.yaml": "orchestrator-rules",
        "extraction/plugins.yaml": "extraction-plugins",
        "extraction/enrichment.yaml": "extraction-enrichment",
        "extraction/embedding.yaml": "extraction-embedding",
        "tlr/approaches.yaml": "tlr-approaches",
        "inconsistency/plugins.yaml": "inconsistency-plugins",
        "query/plugins.yaml": "query-plugins",
        "query/intents.yaml": "query-intents",
        "query/examples.yaml": "query-examples",
        "query/strategies.yaml": "query-strategies",
    }
    config_registry = registry.with_resources(
        (s["$id"], Resource.from_contents(s))
        for s in (json.loads(f.read_text()) for f in config_dir.glob("*.schema.json"))
    )
    for rel, name in config_schemas.items():
        schema_file = config_dir / f"{name}.schema.json"
        check(errors, f"config/{name}.schema.json missing", schema_file.exists())
        if not schema_file.exists():
            continue
        schema = json.loads(schema_file.read_text())
        errors.extend(
            f"{schema_file.name}: {e.message}"
            for e in Draft202012Validator(Draft202012Validator.META_SCHEMA).iter_errors(schema)
        )
        if (repo_config / rel).exists():
            instance = yaml.safe_load((repo_config / rel).read_text()) or {}
            errors.extend(
                f"config/{rel}: {e.message}"
                for e in Draft202012Validator(schema, registry=config_registry).iter_errors(instance)
            )

    for path in sorted((contracts / "openapi").glob("*.yaml")):
        try:
            spec, _ = read_from_filename(str(path.resolve()))
            validate_openapi(spec, base_uri=path.resolve().as_uri())
        except Exception as e:  # noqa: BLE001 - report every invalid document, keep going
            errors.append(f"{path.name}: {e}")

    kg = yaml.safe_load((contracts / "kg/schema.yaml").read_text())
    check(errors, "kg/schema.yaml: nodes and relationships", bool(kg.get("nodes")) and bool(kg.get("relationships")))
    return errors


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "contracts"
    problems = main(root)
    for p in problems:
        print(f"FAIL {p}")
    print("contracts ok" if not problems else f"{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)
