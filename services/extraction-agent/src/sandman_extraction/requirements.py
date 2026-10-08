"""Requirements extractor: one Requirement per id heading (Markdown) or per row (CSV) (T1.3)."""

import csv
import io
import re
from pathlib import PurePosixPath
from typing import Any

from markdown_it import MarkdownIt

from sandman_extraction.bounds import add_warnings, clean_kind
from sandman_extraction.stub import FILE_KEY, stub_elements
from sandman_extraction.text import MAX_INPUT_BYTES, NAME_MAX, SECTION_TEXT_MAX

MAX_ROWS = 100_000
MAX_REQUIREMENTS = 10_000
REQ_ID = re.compile(r"[A-Za-z][\w.-]*\d")
REQUIRED_COLUMNS = ("id", "text")


def extract(path: str, content: bytes, fmt: str | None, warnings: list[str]) -> list[dict[str, Any]]:
    """File element plus Requirements; skipped entries are reported in warnings."""
    if len(content) > MAX_INPUT_BYTES:
        raise ValueError(f"{path} has {len(content)} bytes, limit is {MAX_INPUT_BYTES}")
    suffix = PurePosixPath(path).suffix.lower()
    found: list[str] = []
    if fmt == "csv" or suffix == ".csv":
        elements = _csv(path, content, found)
    elif fmt == "markdown" or suffix in (".md", ".markdown"):
        elements = _markdown(path, content, found)
    else:
        return stub_elements(path, "requirements", content)
    add_warnings(warnings, found)
    return elements


def _requirement(key: str, name: str, text: str, kind: str, start: int, end: int) -> dict[str, Any]:
    return {"localKey": key, "label": "Requirement", "kind": kind, "name": name, "text": text[:SECTION_TEXT_MAX],
            "startLine": start, "endLine": end, "parent": FILE_KEY}  # fmt: skip


def _markdown(path: str, content: bytes, warnings: list[str]) -> list[dict[str, Any]]:
    elements = stub_elements(path, "requirements", content)
    text = content.decode("utf-8", errors="replace").removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.removesuffix("\n").split("\n")
    tokens = MarkdownIt("commonmark").parse(text)
    headings = [(t.map[0], tokens[n + 1].content) for n, t in enumerate(tokens)
                if t.type == "heading_open" and t.level == 0 and t.markup[0] == "#" and t.map]  # fmt: skip
    seen: set[str] = set()
    for (i, title), end in zip(headings, [h[0] for h in headings[1:]] + [len(lines)], strict=True):
        parts = title.split(None, 1)
        if not parts or not REQ_ID.fullmatch(parts[0]) or parts[0] == FILE_KEY:
            continue
        if parts[0] in seen:
            warnings.append(f"{path}:{i + 1}: duplicate requirement id {parts[0]} skipped")
            continue
        if len(seen) >= MAX_REQUIREMENTS:
            warnings.append(f"{path}: more than {MAX_REQUIREMENTS} requirements, rest skipped")
            break
        seen.add(parts[0])
        name = (parts[1].strip() if len(parts) > 1 else parts[0])[:NAME_MAX]
        elements.append(_requirement(parts[0], name, "\n".join(lines[i:end]).strip(), "requirement", i + 1, end))
    return elements


def _csv(path: str, content: bytes, warnings: list[str]) -> list[dict[str, Any]]:
    text = content.decode("utf-8", errors="replace").removeprefix("\ufeff")
    elements = stub_elements(path, "requirements", content)
    elements[0]["text"] = text[:500]
    elements[0]["endLine"] = max(len(text.splitlines()), 1)
    reader = csv.reader(io.StringIO(text, newline=""))
    columns: dict[str, int] = {}
    id_col = text_col = -1
    seen: set[str] = set()
    previous = rows = 0
    for row in reader:
        start, previous = previous + 1, reader.line_num
        if not any(c.strip() for c in row):
            continue
        if not columns:
            for n, c in enumerate(row):
                columns.setdefault(c.strip().lower(), n)
            if missing := [c for c in REQUIRED_COLUMNS if c not in columns]:
                raise ValueError(f"{path}: missing CSV column(s) {', '.join(missing)}")
            id_col, text_col = columns["id"], columns["text"]
            title_col, type_col = columns.get("title", -1), columns.get("type", -1)
            continue
        rows += 1
        if rows > MAX_ROWS:
            raise ValueError(f"{path} has more than {MAX_ROWS} rows")
        key = _cell(row, id_col)
        if not key or key == FILE_KEY:
            warnings.append(f"{path}:{start}: row without usable id skipped")
        elif key in seen:
            warnings.append(f"{path}:{start}: duplicate requirement id {key} skipped")
        else:
            seen.add(key)
            body = _cell(row, text_col)
            kind = _cell(row, type_col) or "requirement"
            if (clean := clean_kind(kind)) is None:
                warnings.append(f"{path}:{start}: invalid type for {key} dropped")
                clean = "requirement"
            name = (_cell(row, title_col) or body)[:NAME_MAX]
            elements.append(_requirement(key, name, body, clean, start, reader.line_num))
    return elements


def _cell(row: list[str], n: int) -> str:
    return row[n].strip() if 0 <= n < len(row) else ""
