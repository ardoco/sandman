import json
from pathlib import Path

from sandman_agent import classify

CASES = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "classification" / "cases.json").read_text())


def test_golden_cases() -> None:
    assert CASES["classifierVersion"] == classify.CLASSIFIER_VERSION
    for c in CASES["cases"]:
        v = classify.resolve(c["path"], c["head"].encode(), c["rules"])
        assert {"kind": v.kind, "format": v.format, "source": v.source, "reason": v.reason, "weak": v.weak} == c[
            "expected"
        ], c["path"]


def test_cli_accepts_every_rule_kind() -> None:
    from sandman_cli.__main__ import KINDS

    assert KINDS == classify.RULE_KINDS and "documentation" in KINDS and "ignored" in KINDS
