import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sandman_agent import classify
from sandman_extraction import ingest

CASES = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "classification" / "cases.json").read_text())


def test_golden_cases() -> None:
    assert CASES["classifierVersion"] == classify.CLASSIFIER_VERSION
    for c in CASES["cases"]:
        v = classify.resolve(c["path"], c["head"].encode(), c["rules"])
        assert {"kind": v.kind, "format": v.format, "source": v.source, "reason": v.reason, "weak": v.weak} == c[
            "expected"
        ], c["path"]


def test_manifest_accepts_documentation_and_rejects_ignored_rule() -> None:
    assert ingest.KindRule(glob="docs/**", kind="documentation").kind == "documentation"
    with pytest.raises(ValidationError):
        ingest.KindRule(glob="legacy/**", kind="ignored")
