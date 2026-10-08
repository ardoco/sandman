"""Golden files of built-in extractors (T1.1; T1.2-T1.4 add fixtures). SANDMAN_UPDATE_GOLDEN=1 rewrites them."""

import json
import os
from pathlib import Path

import pytest
from sandman_extraction import builtin
from sandman_extraction.text import render_sentences

TESTS = Path(__file__).parent
FIXTURES, GOLDEN = TESTS / "fixtures", TESTS / "golden"
TEXT_SUFFIXES = (".md", ".txt", ".csv")
CASES = sorted(
    p.relative_to(FIXTURES).as_posix()
    for root in ("mini", "benchmark", "text", "requirements")
    for p in (FIXTURES / root).rglob("*")
    if p.suffix in TEXT_SUFFIXES and p.name != "SOURCES.txt"
)


def kind_of(path: str) -> str:
    return "requirements" if "/requirements/" in f"/{path}" else "architecture-doc"


@pytest.mark.parametrize("case", CASES)
def test_golden(case: str) -> None:
    warnings: list[str] = []
    elements, relations = builtin.extract(case, kind_of(case), None, (FIXTURES / case).read_bytes(), warnings)
    result = {"elements": elements, "relations": relations} | ({"warnings": warnings} if warnings else {})
    actual = json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False)
    golden = GOLDEN / f"{case}.json"
    if os.environ.get("SANDMAN_UPDATE_GOLDEN"):
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual + "\n", encoding="utf-8")
    assert golden.read_text(encoding="utf-8") == actual + "\n"
    assert render_sentences(elements).count("\n") == sum(e.get("sentenceIndex") is not None for e in elements)


def test_no_orphan_golden_files() -> None:
    cases = set(CASES)
    orphans = [p for p in GOLDEN.rglob("*.json") if p.relative_to(GOLDEN).as_posix().removesuffix(".json") not in cases]
    assert not orphans
