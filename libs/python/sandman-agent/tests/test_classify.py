import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from sandman_agent import classify

ROOT = Path(__file__).resolve().parents[4]
CASES = json.loads((ROOT / "contracts" / "classification" / "cases.json").read_text())

CSV = b"ID,Text\n1,x\n"
REQS = b"# REQ-1 A\nx\n# FR-12 B\ny\n"


@pytest.mark.parametrize(
    ("path", "head", "expected"),
    [
        ("model/a.repository", b"", ("architecture-model", "pcm-repository")),
        ("a.xmi", b"", ("architecture-model", "uml")),
        ("src/test/java/A.java", b"", ("test", None)),
        ("src/FooTest.java", b"", ("test", None)),
        ("pkg/test_a.py", b"", ("test", None)),
        ("web/a.spec.ts", b"", ("test", None)),
        ("src/main/A.java", b"", ("code", None)),
        ("tests/data.csv", CSV, ("requirements", "csv")),
        ("data.csv", b"a,b\n", ("other", None)),
        ("requirements/r.md", b"", ("requirements", None)),
        ("srs.pdf", b"", ("requirements", None)),
        ("notes.md", REQS, ("requirements", None)),
        ("notes.md", b"# REQ-1 A\n", ("other", None)),
        ("architecture.md", b"", ("architecture-doc", None)),
        ("adr/0001.md", b"", ("architecture-doc", "adr")),
        ("decisions/0001.txt", b"", ("architecture-doc", None)),
        ("docs/guide.md", b"", ("documentation", None)),
        ("README.md", b"# hi\n", ("documentation", None)),
        ("logo.png", b"", ("other", None)),
    ],
)
def test_guess(path: str, head: bytes, expected: tuple[str, str | None]) -> None:
    assert classify.guess(path, head) == expected


def test_legacy_guess_keeps_old_docs_dir_kind() -> None:
    assert classify.legacy_guess("docs/guide.md", b"") == ("architecture-doc", None)
    assert classify.legacy_guess("README.md", b"") == ("other", None)


def outputs() -> list[dict[str, Any]]:
    return [
        {"kind": v.kind, "format": v.format, "source": v.source, "reason": v.reason, "weak": v.weak}
        for v in (classify.resolve(c["path"], c["head"].encode(), c["rules"]) for c in CASES["cases"])
    ]


def test_golden_cases() -> None:
    assert outputs() == [c["expected"] for c in CASES["cases"]]
    assert CASES["classifierVersion"] == classify.CLASSIFIER_VERSION


def test_changed_outputs_need_new_classifier_version() -> None:
    digest = hashlib.sha256(json.dumps(outputs(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert CASES["versionHashes"].get(str(classify.CLASSIFIER_VERSION)) == digest, (
        "classifier outputs changed: raise CLASSIFIER_VERSION and add its hash to cases.json versionHashes"
    )


def test_rule_beats_heuristic_and_head_is_lazy() -> None:
    rules = [{"glob": "**/*.java", "kind": "other", "format": None}]

    def no_head() -> bytes:
        raise AssertionError("head read despite rule")

    v = classify.resolve("a/B.java", no_head, rules)
    assert (v.kind, v.source, v.reason, v.weak) == ("other", "rule", "rule **/*.java", False)
    assert classify.resolve("a/B.py", b"", rules).source == "heuristic"
    assert classify.resolve("a.bin", b"").weak


def test_kinds_equal_contract_and_java() -> None:
    schema = json.loads((ROOT / "contracts" / "common.schema.json").read_text())
    java = ROOT / "services/tlr-agent/approach-spi/src/main/java/edu/kit/kastel/mcse/ardoco/sandman/tlr/KindPair.java"
    listed = re.search(r"KINDS\s*=\s*List\.of\(([^)]*)\)", java.read_text())
    assert listed is not None
    assert set(classify.ARTIFACT_KINDS) == set(schema["$defs"]["ArtifactKind"]["enum"])
    assert set(classify.ARTIFACT_KINDS) == set(re.findall(r'"([^"]+)"', listed.group(1)))
    assert classify.RULE_KINDS == (*classify.ARTIFACT_KINDS, "ignored")


def test_knowledge_file_is_a_strong_other_verdict() -> None:
    v = classify.resolve("sandman-knowledge.yaml", lambda: b"")
    assert (v.kind, v.format, v.source, v.weak) == ("other", "sandman-knowledge", "rule", False)
