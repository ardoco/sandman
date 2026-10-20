"""Decision records (Nygard ADR, MADR v2 to v4): the file element becomes a Decision (T7.1)."""

import re
from datetime import date
from pathlib import PurePosixPath
from typing import Any

import yaml

from sandman_extraction import text

DECISION_MAX = 4000
DECISION_SECTIONS = ("decision", "decision outcome")
CONSEQUENCE_SECTIONS = ("consequences",)
STATUSES = ("proposed", "accepted", "rejected", "deprecated", "superseded")
_FRONT = re.compile(r"\A---[ \t]*\n(.*?\n)?---[ \t]*(?:\n|\Z)", re.DOTALL)
_TITLE = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_NUMBER = re.compile(r"^(?:ADR[-\s]?)?\d+\s*[.:\-]\s*", re.IGNORECASE)
_LINE = r"^[ \t]*(?:[*+-][ \t]+)?\**{}\**[ \t]*:\**[ \t]*(\S.*?)[ \t]*$"
_STATUS_LINE = re.compile(_LINE.format("Status"), re.MULTILINE | re.IGNORECASE)
_DATE_LINE = re.compile(_LINE.format("Date"), re.MULTILINE | re.IGNORECASE)


def _split_front(source: str) -> tuple[dict[str, Any], str]:
    """(front matter mapping, source with the front matter lines replaced by empty lines)."""
    found = _FRONT.match(source)
    if not found:
        return {}, source
    try:
        data = yaml.load(found.group(1) or "", Loader=yaml.BaseLoader)  # noqa: S506 - BaseLoader builds strings only
    except yaml.YAMLError:
        data = None
    return data if isinstance(data, dict) else {}, "\n" * found.group(0).count("\n") + source[found.end() :]


def _status(value: str) -> str:
    words = re.findall(r"[a-z]+", value.lower())
    return words[0] if words and words[0] in STATUSES else "unknown"


def _date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        return None


def _body(elements: list[dict[str, Any]], names: tuple[str, ...]) -> list[str]:
    """Own text of the sections with these names, without the heading line."""
    return [
        s["text"].partition("\n")[2].strip()
        for s in elements
        if s["kind"] == "section" and s["name"].lower() in names
    ]


def extract(path: str, content: bytes, warnings: list[str] | None = None) -> list[dict[str, Any]]:
    """Elements of `text.extract` (Markdown) with the file element as Decision."""
    source = content.decode("utf-8", errors="replace").removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    front, source = _split_front(source)
    elements = text.extract(path, source.encode(), markdown=True)
    file = elements[0]
    heading = _TITLE.search(source)
    title = _NUMBER.sub("", heading.group(1)).strip() if heading else ""
    file["name"] = title or (heading.group(1) if heading else PurePosixPath(path).stem)

    raw_status = front.get("status")
    if raw_status is None and (line := _STATUS_LINE.search(source)):
        raw_status = line.group(1)
    if raw_status is None:
        section = next((s for s in elements if s["kind"] == "section" and s["name"].lower() == "status"), None)
        raw_status = next((e["text"] for e in elements if section and e.get("parent") == section["localKey"]
                           and e["kind"] == "sentence"), "")  # fmt: skip
    file["decisionStatus"] = _status(str(raw_status))
    raw_date = front.get("date")
    if raw_date is None and (line := _DATE_LINE.search(source)):
        raw_date = line.group(1)
    if raw_date is not None and (iso := _date(raw_date)):
        file["decisionDate"] = iso

    decision = _body(elements, DECISION_SECTIONS)
    if not decision and warnings is not None:
        warnings.append(f"adr: no decision section in {path}")
    file["label"], file["kind"] = "Decision", "decision"
    file["text"] = "\n\n".join(p for p in [file["name"], *decision, *_body(elements, CONSEQUENCE_SECTIONS)] if p)[
        :DECISION_MAX
    ]
    return elements
