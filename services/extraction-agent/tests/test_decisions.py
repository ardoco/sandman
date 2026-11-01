"""T7.1 unit tests of the decision record extractor (no Docker)."""

from pathlib import Path
from typing import Any

import pytest
from sandman_extraction import builtin, decisions, text

ADR_DIR = Path(__file__).resolve().parents[3] / "adr"

NYGARD = """# 12. Use Memgraph

## Status

Accepted

## Context

We need a graph store.

## Decision

We use Memgraph.

## Consequences

Cypher is available.
"""

MADR3 = """---
status: "accepted"
date: 2026-10-06
---

# Use A2A

## Context and Problem Statement

How do agents talk?

## Decision Outcome

Chosen option: A2A.

### Consequences

* Good, one protocol.
"""


def run(md: str, path: str = "adr/0001-x.md") -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    return decisions.extract(path, md.encode(), warnings), warnings


def test_nygard() -> None:
    els, warnings = run(NYGARD)
    file = els[0]
    assert (file["label"], file["kind"], file["localKey"]) == ("Decision", "decision", "file")
    assert file["name"] == "Use Memgraph"
    assert file["decisionStatus"] == "accepted" and "decisionDate" not in file
    assert file["text"] == "Use Memgraph\n\nWe use Memgraph.\n\nCypher is available."
    assert warnings == []


def test_madr3_front_matter() -> None:
    els, _ = run(MADR3)
    file = els[0]
    assert (file["decisionStatus"], file["decisionDate"]) == ("accepted", "2026-10-06")
    assert file["text"] == "Use A2A\n\nChosen option: A2A.\n\n* Good, one protocol."
    assert not any("status" in e["text"] or "2026" in e["text"] for e in els if e["kind"] == "sentence")
    assert min(e["startLine"] for e in els if e["kind"] == "section" and e["name"] == "Use A2A") == 6


def test_madr2_bullets() -> None:
    els, _ = run("# Use X\n\n* Status: proposed\n* Date: 2020-01-02\n\n## Decision Outcome\n\nX.\n")
    assert (els[0]["decisionStatus"], els[0]["decisionDate"]) == ("proposed", "2020-01-02")


def test_superseded() -> None:
    els, _ = run(NYGARD.replace("Accepted", "Superseded by ADR-7"))
    assert els[0]["decisionStatus"] == "superseded"
    assert any("Superseded by ADR-7" in e["text"] for e in els if e["kind"] == "sentence")


def test_unknown_status_and_invalid_date() -> None:
    els, _ = run("---\nstatus: maybe\ndate: 2026-13-45\n---\n# T\n\n## Decision\n\nD.\n")
    assert els[0]["decisionStatus"] == "unknown" and "decisionDate" not in els[0]


def test_no_decision_section_keeps_element_and_warns() -> None:
    els, warnings = run("# ADR-3: Only context\n\n## Context\n\nText.\n", "adr/0003-only-context.md")
    assert els[0]["label"] == "Decision" and els[0]["name"] == "Only context"
    assert warnings == ["adr: no decision section in adr/0003-only-context.md"]


def test_name_falls_back_to_file_stem() -> None:
    els, _ = run("No heading.\n", "adr/0004-plain.md")
    assert els[0]["name"] == "0004-plain"


def test_unterminated_front_matter_is_plain_text() -> None:
    els, _ = run("---\nstatus: accepted\n# T\n")
    assert els[0]["label"] == "Decision" and els[0]["name"] == "T"


def test_builtin_dispatch() -> None:
    els, rels = builtin.extract("adr/0001-x.md", "architecture-doc", "adr", NYGARD.encode())
    assert els[0]["label"] == "Decision" and rels == []


@pytest.mark.parametrize("path", sorted(ADR_DIR.glob("0*.md")))
def test_repository_adrs(path: Path) -> None:
    els, _ = run(path.read_text(), f"adr/{path.name}")
    plain = text.extract(f"adr/{path.name}", path.read_bytes())
    assert els[0]["label"] == "Decision"
    expected = "proposed" if path.name.startswith("0015") else "accepted"
    assert els[0]["decisionStatus"] == expected
    own = {e["localKey"] for e in els if e["kind"] == "sentence"}
    assert own <= {e["localKey"] for e in plain if e["kind"] == "sentence"}


def test_repository_adr_count() -> None:
    assert len(list(ADR_DIR.glob("0*.md"))) == 18
