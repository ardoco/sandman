from pathlib import Path

import pytest
import yaml
from sandman_query.guard import GuardError, check

CORPUS = yaml.safe_load((Path(__file__).parent / "corpus.yaml").read_text())
ANCHOR = "(n:Element {projectId: $projectId})"


def expand(text: str) -> str:
    return text.replace("@A@", ANCHOR)


REJECT = [pytest.param(e, id=e["id"]) for e in CORPUS["reject"]]
MUTATIONS = {
    "upper": str.upper,
    "lower": str.lower,
    "comments": lambda q: q.replace(" ", " /**/ "),
    "newline": lambda q: q.replace(" ", "\n"),
    "leading_comment": lambda q: "/* x */ // y\n" + q,
    "semicolon": lambda q: q + ";",
}


def test_corpus_shape() -> None:
    assert CORPUS["version"] == 1
    entries = CORPUS["reject"] + CORPUS["bounded"] + CORPUS["injection"]
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids))
    assert len(CORPUS["reject"]) + len(CORPUS["bounded"]) >= 80
    assert len(CORPUS["injection"]) >= 8
    assert all(e["threat"].startswith("TH-") and e["why"] for e in entries)


@pytest.mark.parametrize("entry", REJECT)
def test_reject_entry_refused(entry: dict) -> None:
    with pytest.raises(GuardError, match=entry["message"] or ""):
        check(expand(entry["query"]))


@pytest.mark.parametrize("mutation", MUTATIONS)
@pytest.mark.parametrize("entry", REJECT)
def test_reject_entry_refused_after_mutation(entry: dict, mutation: str) -> None:
    with pytest.raises(GuardError):
        check(MUTATIONS[mutation](expand(entry["query"])))


@pytest.mark.parametrize("entry", [pytest.param(e, id=e["id"]) for e in CORPUS["bounded"]])
def test_bounded_entry_accepted_by_guard(entry: dict) -> None:
    check(entry["query"])


def test_oversized_query_is_refused_before_tokenizing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(src: str) -> None:
        raise AssertionError("tokenized an oversized query")

    monkeypatch.setattr("sandman_query.guard.tokenize", fail)
    with pytest.raises(GuardError, match="longer than"):
        check("RETURN " + "1" * 2_000_000)
