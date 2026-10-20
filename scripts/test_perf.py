"""Harness helpers of scripts/perf.py (no Docker needed)."""

from pathlib import Path

import pytest
from perf import count_workload, evaluate, file_mix, generate, mib, percentile, workflow_failures


def test_generate_is_deterministic_and_has_the_requested_size(tmp_path: Path) -> None:
    first = generate(tmp_path / "a", 200)
    second = generate(tmp_path / "b", 200)
    assert len(first) == 201
    assert [p.read_text() for p in first] == [p.read_text() for p in second]
    assert len(list((tmp_path / "a").glob("src/main/java/**/*.java"))) == 200 - 8 - 40 - 2 - 10 - 20


def test_mib_and_percentile() -> None:
    assert mib("1.5GiB") == 1536
    assert mib("512KiB") == 0.5
    assert mib("2MiB") == 2
    assert percentile([float(i) for i in range(1, 101)], 0.95) == pytest.approx(95)


def _result(files: int, delta: int, seconds: dict[str, float], status: str = "SUCCEEDED") -> dict:
    root = Path("/p")
    paths = [root / f"docs/{i}.md" for i in range(files // 5)] + [root / f"model/{i}.repository" for i in range(2)]
    steps = {f"{k}#0": {"status": "SUCCEEDED", "seconds": v} for k, v in seconds.items()}
    phase = {
        "uploadSeconds": 1,
        "endToEndSeconds": seconds["e2e"],
        "timedOut": False,
        "workflows": [{"status": status, "steps": steps}],
    }
    return {"workload": count_workload(paths, root, delta), "ingest": phase, "incremental": phase}


def test_reference_workload_gives_the_per_unit_limits() -> None:
    paths = generate_paths(1000)
    assert count_workload(paths, Path("/p"), 100) == {"documents": 200, "models": 10, "pairs": 2000, "delta": 100}


def generate_paths(files: int) -> list[Path]:
    n_doc, n_model = file_mix(files)[1:3]
    return [Path(f"/p/docs/{i}") for i in range(n_doc)] + [Path(f"/p/model/{i}") for i in range(n_model)]


def test_per_unit_verdict_uses_the_counts_of_the_run() -> None:
    # 40 documents, 2 models: 80 pairs; ingest-e2e limit 4.5 s/document, detection 0.06 s/pair
    seconds = {"e2e": 100, "recover-trace-links": 10, "detect-inconsistencies": 4}
    rows = {r["id"]: r for r in evaluate(_result(200, 20, seconds))}
    assert rows["ingest-e2e"]["perUnit"] == {"unit": "s/document", "limit": 4.5, "value": 2.5, "verdict": "PASS"}
    assert rows["detection"]["perUnit"]["limit"] == 0.06
    assert rows["detection"]["perUnit"]["value"] == 0.05
    assert rows["ingest-tlr"]["perUnit"]["value"] == round(10 / 120, 4)
    assert rows["incremental-e2e"]["perUnit"]["limit"] == 3
    assert rows["ingest-tlr"]["verdict"] == rows["ingest-tlr"]["perUnit"]["verdict"]


def test_per_unit_fail_overrides_absolute_pass() -> None:
    seconds = {"e2e": 800, "recover-trace-links": 10, "detect-inconsistencies": 4}
    row = {r["id"]: r for r in evaluate(_result(200, 20, seconds))}["ingest-e2e"]
    assert row["absoluteVerdict"] == "PASS"
    assert row["verdict"] == "FAIL"


def test_workflow_not_succeeded_is_reported() -> None:
    seconds = {"e2e": 1, "recover-trace-links": 1, "detect-inconsistencies": 1}
    assert workflow_failures(_result(200, 20, seconds)) == []
    assert workflow_failures(_result(200, 20, seconds, "FAILED")) == ["ingest: FAILED", "incremental: FAILED"]


def test_impact_budget_uses_the_max_of_the_repetitions() -> None:
    result = _result(200, 20, {"e2e": 10})
    result["incremental"] = {**result["incremental"], "impact": {"maxSeconds": 2.5}}
    assert {r["id"]: r["verdict"] for r in evaluate(result)}["impact-depth2"] == "FAIL"
    result["incremental"]["impact"]["maxSeconds"] = 0.4
    assert {r["id"]: r["verdict"] for r in evaluate(result)}["impact-depth2"] == "PASS"
