"""Sentence alignment against ArDoCo benchmark texts (one sentence per line); prints the rate per project."""

from pathlib import Path

import pytest
from sandman_extraction.text import extract, render_sentences

FIXTURES = Path(__file__).parent / "fixtures" / "benchmark"


def aligned(text: bytes) -> tuple[int, int]:
    gold = text.decode("utf-8").splitlines()
    got = render_sentences(extract("t.txt", text)).splitlines()
    return sum(a == b for a, b in zip(got, gold, strict=False)), len(gold)


@pytest.mark.parametrize("project", ["teastore", "mediastore", "teammates", "bigbluebutton", "jabref"])
def test_vendored_alignment(project: str, capsys: pytest.CaptureFixture[str]) -> None:
    ok, total = aligned((FIXTURES / project / f"{project}.txt").read_bytes())
    with capsys.disabled():
        print(f"\nsentence alignment {project}: {ok}/{total}")
    assert ok == total

