"""Requirements extractor unit tests (T1.3)."""

import pytest
from sandman_extraction import builtin
from sandman_extraction.requirements import MAX_ROWS
from sandman_extraction.text import MAX_INPUT_BYTES


def reqs(path: str, content: str | bytes) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    data = content if isinstance(content, bytes) else content.encode()
    elements, _ = builtin.extract(path, "requirements", None, data, warnings)
    assert elements[0]["kind"] == "file" and elements[0]["label"] == "TextUnit"
    return [e for e in elements if e["label"] == "Requirement"], warnings


def test_markdown_levels_ids_and_bodies() -> None:
    found, warnings = reqs(
        "r.md",
        "# Title\n# REQ-1 First\nbody\n### FR.2 Second\nmore\n#### sub heading\nstill\n## NOID heading\n## R3\nx\n",
    )
    assert [(r["localKey"], r["name"], r["startLine"], r["endLine"]) for r in found] == [
        ("REQ-1", "First", 2, 3),
        ("FR.2", "Second", 4, 5),
        ("R3", "R3", 9, 10),
    ]
    assert found[0]["text"] == "# REQ-1 First\nbody" and found[0]["parent"] == "file" and not warnings


def test_markdown_duplicate_id_warns() -> None:
    found, warnings = reqs("r.md", "## A-1 x\n## A-1 y\n")
    assert [r["name"] for r in found] == ["x"] and "duplicate" in warnings[0]


def test_csv_quoting_bom_crlf_and_lines() -> None:
    content = (
        b'\xef\xbb\xbfid,TEXT,Type\r\nA1,"line one\r\nline two",quality\r\n\r\nA2,plain\r\n,orphan,x\r\nA2,again,x\r\n'
    )
    found, warnings = reqs("r.csv", content)
    assert [(r["localKey"], r["kind"], r["startLine"], r["endLine"]) for r in found] == [
        ("A1", "quality", 2, 3),
        ("A2", "requirement", 5, 5),
    ]
    assert found[0]["text"] == "line one\r\nline two" and found[1]["name"] == "plain"
    assert len(warnings) == 2 and "without usable id" in warnings[0] and "duplicate" in warnings[1]


def test_csv_empty_and_header_only() -> None:
    assert reqs("r.csv", "") == ([], [])
    assert reqs("r.csv", "id,text\n") == ([], [])


def test_csv_missing_column_and_limits_raise() -> None:
    with pytest.raises(ValueError, match="missing CSV column"):
        reqs("r.csv", "id,title\nA1,x\n")
    with pytest.raises(ValueError, match="rows"):
        reqs("r.csv", "id,text\n" + "".join(f"R{i},t\n" for i in range(MAX_ROWS + 1)))
    with pytest.raises(Exception, match="field larger"):
        reqs("r.csv", 'id,text\nA1,"' + "x" * 200_000 + '"\n')
    with pytest.raises(ValueError, match="limit"):
        reqs("r.md", b"x" * (MAX_INPUT_BYTES + 1))


def test_other_formats_yield_file_only() -> None:
    assert reqs("r.txt", "REQ-1 something\n") == ([], [])
