"""Sentence alignment against ArDoCo benchmark texts (one sentence per line); prints the rate per project."""

import os
from pathlib import Path

import pytest
from sandman_extraction.text import extract, render_sentences

FIXTURES = Path(__file__).parent / "fixtures" / "benchmark"
EXTERNAL = {"mediastore": "mediastore/text_2016/mediastore.txt", "teastore": "teastore/text_2020/teastore.txt"}


def aligned(text: bytes) -> tuple[int, int]:
    gold = text.decode("utf-8").splitlines()
    got = render_sentences(extract("t.txt", text)).splitlines()
    return sum(a == b for a, b in zip(got, gold, strict=False)), len(gold)


def test_vendored_teastore_alignment(capsys: pytest.CaptureFixture[str]) -> None:
    ok, total = aligned((FIXTURES / "teastore" / "teastore.txt").read_bytes())
    with capsys.disabled():
        print(f"\nsentence alignment teastore: {ok}/{total}")
    assert ok == total


@pytest.mark.skipif(not os.environ.get("SANDMAN_BENCHMARK_DIR"), reason="SANDMAN_BENCHMARK_DIR not set")
def test_mediastore_alignment(capsys: pytest.CaptureFixture[str]) -> None:
    ok, total = aligned((Path(os.environ["SANDMAN_BENCHMARK_DIR"]) / EXTERNAL["mediastore"]).read_bytes())
    with capsys.disabled():
        print(f"\nsentence alignment mediastore: {ok}/{total}")
    assert ok == total
