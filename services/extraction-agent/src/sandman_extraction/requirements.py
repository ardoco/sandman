"""Requirements extractor: one Requirement per id heading (Markdown) or per row (CSV) (T1.3)."""

import csv
import io
import re
from pathlib import PurePosixPath
from typing import Any

from sandman_extraction.stub import FILE_KEY, stub_elements
from sandman_extraction.text import MAX_INPUT_BYTES, NAME_MAX, SECTION_TEXT_MAX

MAX_ROWS = 100_000
REQ_ID = re.compile(r"[A-Za-z][\w.-]*\d")
HEADING = re.compile(r"#{1,6}(?:\s|$)")
REQUIRED_COLUMNS = ("id", "text")


def extract(path: str, content: bytes, fmt: str | None, warnings: list[str]) -> list[dict[str, Any]]:
    """File element plus Requirements; skipped entries are reported in warnings."""
    if len(content) > MAX_INPUT_BYTES:
        raise ValueError(f"{path} has {len(content)} bytes, limit is {MAX_INPUT_BYTES}")
    suffix = PurePosixPath(path).suffix.lower()
    if fmt == "csv" or suffix == ".csv":
        return _csv(path, content, warnings)
    if fmt == "markdown" or suffix in (".md", ".markdown"):
        return _markdown(path, content, warnings)
    return stub_elements(path, "requirements", content)


def _requirement(key: str, name: str, text: str, kind: str, start: int, end: int) -> dict[str, Any]:
    return {"localKey": key, "label": "Requirement", "kind": kind, "name": name, "text": text[:SECTION_TEXT_MAX],
            "startLine": start, "endLine": end, "parent": FILE_KEY}  # fmt: skip


def _markdown(path: str, content: bytes, warnings: list[str]) -> list[dict[str, Any]]:
    elements = stub_elements(path, "requirements", content)
    lines = content.decode("utf-8", errors="replace").removeprefix("﻿").splitlines()
    headings = [i for i, line in enumerate(lines) if HEADING.match(line)]
    seen: set[str] = set()
    for i in headings:
        parts = lines[i].lstrip("#").split(None, 1)
        if not parts or not REQ_ID.fullmatch(parts[0]) or parts[0] == FILE_KEY:
            continue
        if parts[0] in seen:
            warnings.append(f"{path}:{i + 1}: duplicate requirement id {parts[0]} skipped")
            continue
        seen.add(parts[0])
        end = next((j for j in headings if j > i), len(lines))
        name = parts[1].strip() if len(parts) > 1 else parts[0]
        elements.append(_requirement(parts[0], name, "\n".join(lines[i:end]).strip(), "requirement", i + 1, end))
    return elements


def _csv(path: str, content: bytes, warnings: list[str]) -> list[dict[str, Any]]:
    text = content.decode("utf-8", errors="replace").removeprefix("﻿")
    elements = stub_elements(path, "requirements", content)
    elements[0]["text"] = text[:500]
    elements[0]["endLine"] = max(len(text.splitlines()), 1)
    reader = csv.reader(io.StringIO(text, newline=""))
    header: dict[str, int] = {}
    seen: set[str] = set()
    previous = rows = 0
    for row in reader:
        start, previous = previous + 1, reader.line_num
        if not any(c.strip() for c in row):
            continue
        if not header:
            for n, c in enumerate(row):
                header.setdefault(c.strip().lower(), n)
            if missing := [c for c in REQUIRED_COLUMNS if c not in header]:
                raise ValueError(f"{path}: missing CSV column(s) {', '.join(missing)}")
            continue
        rows += 1
        if rows > MAX_ROWS:
            raise ValueError(f"{path} has more than {MAX_ROWS} rows")
        cells = {c: row[n].strip() if n < len(row) else "" for c, n in header.items()}
        key = cells["id"]
        if not key or key == FILE_KEY:
            warnings.append(f"{path}:{start}: row without usable id skipped")
        elif key in seen:
            warnings.append(f"{path}:{start}: duplicate requirement id {key} skipped")
        else:
            seen.add(key)
            body = cells["text"]
            elements.append(_requirement(key, cells.get("title") or body[:NAME_MAX], body,
                                         cells.get("type") or "requirement", start, reader.line_num))  # fmt: skip
    return elements
