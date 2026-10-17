# ruff: noqa: E501
import json
from pathlib import Path

import pytest
from t24_guided import (
    END,
    R_EQ,
    R_LISSA,
    START,
    build_updates,
    code_files,
    copy_back,
    f1_of,
    failed_steps,
    update_results,
)

DOC = """# T

Status: harness implemented (2026-10-17) · ArDoCo exact equality done (2026-10-17) · LiSSA re-run, variance pilot and contamination probe not yet reported (see Results) · open questions: x

## Results

| Check | Result | Date |
|---|---|---|
| `BenchmarkEquivalenceTest` (adapter against standalone runner) | Passed | 2026-10-17 |
| LiSSA live re-run with Δ and CI | Not reported | – |

- note
"""


def results() -> dict:
    return {
        "3": {"status": "PASS", "date": "2026-10-18", "pairs": [{"name": "mediastore-ms.repository", "equal": True}]},
        "4": {
            "status": "PASS",
            "date": "2026-10-18",
            "f1": [0.7, 0.72],
            "mean": 0.71,
            "published": 0.7,
            "delta": 0.01,
            "ci": [-0.02, 0.04],
            "model": "m",
            "provider": "p",
        },
    }


def test_update_results_wraps_table_and_is_idempotent() -> None:
    once = update_results(DOC, build_updates(results(), "abc"))
    assert once.count(START) == 1 and once.count(END) == 1
    assert "Δ +0.0100" in once and "Exit code 0 for 1 pairs" in once
    assert "| `BenchmarkEquivalenceTest` (adapter against standalone runner) | Passed | 2026-10-17 |" in once
    assert "| Contamination probe | Not reported | – |" in once
    assert once.endswith("\n\n- note\n") and "\n## Results\n" in once
    assert update_results(once, build_updates(results(), "abc")) == once


def test_update_results_keeps_rows_without_new_data_and_updates_status() -> None:
    once = update_results(DOC, build_updates(results(), "abc"))
    twice = update_results(once, {R_EQ: ("Exit code 1 for x", "d")})
    assert "LiSSA live re-run with Δ and CI | Δ +0.0100" in twice
    status = next(line for line in twice.splitlines() if line.startswith("Status:"))
    assert "ArDoCo exact equality reported, check Results" in status
    assert "LiSSA re-run done (2026-10-18)" in status
    assert "variance pilot, contamination probe not yet reported" in status
    assert "guided script" in status and status.endswith("open questions: x")
    assert R_LISSA in twice


def test_f1_of_compare_json() -> None:
    assert f1_of(json.dumps({"a": {"f1": 0.75, "f1Ci": [0.6, 0.8]}})) == 0.75


def test_perturb_copy_back_and_duplicate_guard(tmp_path: Path) -> None:
    inp = tmp_path / "in"
    (inp / "src/a").mkdir(parents=True)
    (inp / "src/a/A.java").write_text("old")
    (inp / "src/B.py").write_text("old")
    rels = code_files(inp)
    assert rels == ["src/B.py", "src/a/A.java"]
    flat, pert = tmp_path / "flat", tmp_path / "pert"
    flat.mkdir()
    (flat / "A.java").write_text("new")
    (flat / "B.py").write_text("new")
    (pert / "src/a").mkdir(parents=True)
    copy_back(flat, pert, rels)
    assert (pert / "src/a/A.java").read_text() == "new" and (pert / "src/B.py").read_text() == "new"
    (inp / "src/b").mkdir()
    (inp / "src/b/A.java").write_text("dup")
    with pytest.raises(ValueError, match="duplicate file names: A.java"):
        code_files(inp)
    with pytest.raises(ValueError, match="no code files"):
        code_files(tmp_path / "empty")


def test_failed_steps_names_step_and_cause() -> None:
    wf = {
        "steps": [
            {"agent": "a", "skill": "ok", "status": "SUCCEEDED"},
            {
                "agent": "tlr",
                "skill": "ardoco",
                "status": "FAILED",
                "error": {"code": "X", "message": "CoreNLP unauthorized"},
            },
        ]
    }
    lines = failed_steps(wf)
    assert len(lines) == 1 and "tlr/ardoco: FAILED X" in lines[0] and "CoreNLP credentials" in lines[0]
