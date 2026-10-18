"""Harness helpers of scripts/perf.py (no Docker needed)."""

from pathlib import Path

import pytest
from perf import generate, mib, percentile


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
