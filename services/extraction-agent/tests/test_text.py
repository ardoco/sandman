"""T1.1 unit tests of the Markdown/text extractor and the built-in dispatch (no Docker)."""

import unicodedata
from pathlib import Path
from typing import Any

import pytest
from sandman_agent.ids import sentence_local_key
from sandman_extraction import builtin
from sandman_extraction.stub import stub_elements
from sandman_extraction.text import MAX_INPUT_BYTES, extract, render_sentences

FIXTURES = Path(__file__).parent / "fixtures"
SPIKE = Path(__file__).resolve().parents[3] / "spikes" / "T0.12" / "fixtures"


def run(md: str, path: str = "a.md") -> list[dict[str, Any]]:
    return extract(path, md.encode())


def keys(els: list[dict[str, Any]], kind: str) -> list[str]:
    return [e["localKey"] for e in els if e["kind"] == kind]


def sentences(els: list[dict[str, Any]]) -> list[str]:
    return [e["text"] for e in els if e["kind"] == "sentence"]


def test_headings_setext_nesting_and_duplicates() -> None:
    els = run("Intro one.\n\nTitle\n=====\n\n## Sub\n\nA.\n\n## Sub\n\nB.\n\nOther\n=====\n\nC.\n\n### Deep\n\nD.\n")
    assert keys(els, "section") == [
        "sec:", "sec:title", "sec:title/sub", "sec:title/sub~2", "sec:other", "sec:other/deep",
    ]  # fmt: skip
    by_key = {e["localKey"]: e for e in els}
    assert by_key["sec:title/sub"]["parent"] == "sec:title" and by_key["sec:title"]["parent"] == "file"
    assert by_key["sec:"]["parent"] == "file"
    assert [e["parent"] for e in els if e["kind"] == "sentence"] == [
        "sec:", "sec:title/sub", "sec:title/sub~2", "sec:other", "sec:other/deep",
    ]  # fmt: skip
    assert by_key["sec:title"]["endLine"] == 13 and by_key["sec:title/sub"]["endLine"] == 9
    assert by_key["sec:title/sub"]["name"] == "Sub" and by_key["sec:title/sub"]["text"].startswith("## Sub")


def test_preamble_absent_when_document_starts_with_heading() -> None:
    assert keys(run("# T\n\nText.\n"), "section") == ["sec:t"]


def test_skipped_level_still_nests_under_last_shallower_heading() -> None:
    els = run("# A\n\n### B\n\nx.\n")
    assert {e["localKey"]: e["parent"] for e in els if e["kind"] == "section"} == {"sec:a": "file", "sec:a/b": "sec:a"}


def test_lists_code_tables_html_and_inline_markup() -> None:
    md = (
        "# T\n\n- First *item*\n- Second [link](http://x.y) item.\n\n```\nnot a sentence.\n```\n\n"
        "    indented code.\n\n| a | b |\n|---|---|\n| c. | d. |\n\n<div>html.</div>\n\n"
        "Uses `code` and **bold** ![img alt](i.png) end.\n"
    )
    assert sentences(run(md)) == ["First item", "Second link item.", "Uses code and bold img alt end."]


def test_abbreviations_and_hard_wrapped_lines() -> None:
    els = run("# T\n\nThe Facade (e.g. the front end)\nhandles requests, i.e. all of them. Next one.\n")
    sents = [e for e in els if e["kind"] == "sentence"]
    assert [s["text"] for s in sents] == [
        "The Facade (e.g. the front end) handles requests, i.e. all of them.", "Next one.",
    ]  # fmt: skip
    assert [(s["startLine"], s["endLine"]) for s in sents] == [(3, 4), (4, 4)]


def test_duplicates_get_occurrence_suffix_and_insertion_changes_no_other_key() -> None:
    before = run("# T\n\nA one.\n\nA one.\n\nB two.\n")
    assert [e["localKey"].rsplit("/", 1)[1][12:] for e in before if e["kind"] == "sentence"] == ["", "~2", ""]
    after = run("# T\n\nNew first.\n\nA one.\n\nA one.\n\nB two.\n")
    old = {e["localKey"]: e["sentenceIndex"] for e in before if e["kind"] == "sentence"}
    new = {e["localKey"]: e["sentenceIndex"] for e in after if e["kind"] == "sentence"}
    assert old.keys() < new.keys() and len(new) == len(old) + 1
    assert all(new[k] == i + 1 for k, i in old.items())


def test_sentence_keys_recomputable_from_text_and_index_is_document_wide() -> None:
    els = run("Pre.\n\n# A\n\nOne. Two.\n\n# B\n\nOne. Two.\n")
    seen: dict[tuple[str, str], int] = {}
    for e in els:
        if e["kind"] == "sentence":
            n = seen[e["parent"], e["text"]] = seen.get((e["parent"], e["text"]), 0) + 1
            assert e["localKey"] == sentence_local_key(e["parent"], e["text"], n)
            assert e["name"] == e["text"][:80]
    assert [e["sentenceIndex"] for e in els if e["kind"] == "sentence"] == [0, 1, 2, 3, 4]


def test_crlf_empty_and_invalid_bytes() -> None:
    assert sentences(run("# T\r\n\r\nOne.\r\nTwo.\r\n")) == ["One.", "Two."]
    assert [e["kind"] for e in run("")] == ["file"] and [e["kind"] for e in run("\n\n  \n")] == ["file"]
    assert sentences(extract("a.md", b"Caf\xe9 is good.\n")) == ["Caf� is good."]


def test_text_normalized_nfc() -> None:
    assert sentences(run("Café  au   lait.\n")) == [unicodedata.normalize("NFC", "Café au lait.")]


def test_plain_text_has_only_preamble_section() -> None:
    els = run("# not a heading.\nSecond line of it.\n\n* not a list. Another.\n", "a.txt")
    assert keys(els, "section") == ["sec:"]
    assert sentences(els) == ["# not a heading.", "Second line of it.", "* not a list.", "Another."]
    assert [(e["startLine"], e["endLine"]) for e in els if e["kind"] == "sentence"] == [(1, 1), (2, 2), (4, 4), (4, 4)]


def test_render_sentences_matches_wrapped_fixture() -> None:
    els = extract("wrapped.md", (FIXTURES / "text" / "wrapped.md").read_bytes())
    rendered = render_sentences(els)
    assert rendered == (SPIKE / "wrapped.sentences.txt").read_text(encoding="utf-8")
    lines = rendered.splitlines()
    ordered = sorted((e for e in els if e["kind"] == "sentence"), key=lambda e: e["sentenceIndex"])
    assert len(lines) == len(ordered)
    for i, (line, e) in enumerate(zip(lines, ordered, strict=True)):
        assert e["sentenceIndex"] == i and e["text"] == line
        assert e["localKey"].split("/")[-1].split("~")[0] == sentence_local_key("", line, 1)[1:]


def test_dispatch_by_format_extension_kind_and_fallback() -> None:
    md = b"# T\n\nOne.\n"
    els, rels = builtin.extract("docs/a.md", "architecture-doc", None, md)
    assert rels == [] and els[0]["label"] == "TextUnit" and keys(els, "sentence")
    assert keys(builtin.extract("docs/a", "architecture-doc", "markdown", md)[0], "sentence")
    assert keys(builtin.extract("docs/a.TXT", "other", None, md)[0], "sentence")
    for path, kind, fmt in [("a.java", "code", None), ("r.md", "requirements", "markdown"), ("a.pdf", "other", "pdf")]:
        assert builtin.extract(path, kind, fmt, md)[0] == stub_elements(path, kind, md)


def test_oversized_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        extract("a.md", b"x" * (MAX_INPUT_BYTES + 1))
