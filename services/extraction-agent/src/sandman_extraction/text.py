"""Markdown and plain-text extractor: file, section and sentence elements with content-based keys (T1.1)."""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from functools import cache
from pathlib import PurePosixPath
from typing import Any

import pysbd  # type: ignore[import-untyped]
from markdown_it import MarkdownIt
from markdown_it.token import Token
from sandman_agent.ids import section_local_key, sentence_local_key

from sandman_extraction.stub import FILE_KEY

SECTION_TEXT_MAX = 4000
MAX_INPUT_BYTES = 5 * 1024 * 1024
NAME_MAX = 80
_WS = re.compile(r"[ \t\n\r\f\v]+")
_BLANK = re.compile(r"\n[ \t]*\n")

Paragraph = tuple[list[tuple[str, int]], int]


@cache
def _segmenter() -> pysbd.Segmenter:
    return pysbd.Segmenter(language="en", clean=False, char_span=True)


def _normalize(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip(" \t\n\r\f\v")


def _pieces(inline: Token) -> list[tuple[str, int]]:
    """Visible text of an inline token with the 0-based source line of each piece (markup removed)."""
    line = inline.map[0] if inline.map else 0
    out: list[tuple[str, int]] = []
    for child in inline.children or []:
        if child.type in ("softbreak", "hardbreak"):
            out.append((" ", line))
            line += 1
        elif child.type in ("text", "code_inline", "image"):
            out.append((child.content, line))
    return out


def _sentences(pieces: list[tuple[str, int]]) -> Iterator[tuple[str, int, int]]:
    """(normalized sentence, 0-based start line, 0-based end line) of one paragraph."""
    text = "".join(p for p, _ in pieces)
    lines = [line for p, line in pieces for _ in p]
    for span in _segmenter().segment(text):
        raw = span.sent
        lead = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if sentence := _normalize(stripped):
            start = span.start + lead
            yield sentence, lines[start], lines[start + len(stripped) - 1]


def _blocks(text: str) -> Iterator[tuple[str, Any]]:
    """Markdown: ('heading', (level, pieces, first line)) and ('paragraph', pieces) in document order."""
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            yield "heading", (int(tok.tag[1]), _pieces(tokens[i + 1]), tok.map[0] if tok.map else 0)
        elif tok.type == "paragraph_open":
            yield "paragraph", _pieces(tokens[i + 1])


def _plain_blocks(text: str) -> Iterator[tuple[str, Any]]:
    offset = 0
    for block in _BLANK.split(text):
        first = text.count("\n", 0, text.index(block, offset))
        offset += len(block)
        if block.strip():
            yield "paragraph", [(line + " ", first + n) for n, line in enumerate(block.split("\n"))]


def _section(path: str, key: str, name: str, parent: str, first: int, level: int) -> dict[str, Any]:
    return {"localKey": key, "label": "TextUnit", "kind": "section", "name": name, "qualifiedName": f"{path}#{key}",
            "startLine": first + 1, "parent": parent, "_level": level, "_first": first}  # fmt: skip


def extract(path: str, content: bytes) -> list[dict[str, Any]]:
    """File element, one section element per heading (plus preamble 'sec:') and one per sentence, in document order."""
    if len(content) > MAX_INPUT_BYTES:
        raise ValueError(f"{path} has {len(content)} bytes, limit is {MAX_INPUT_BYTES}")
    text = content.decode("utf-8", errors="replace").removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    markdown = PurePosixPath(path).suffix.lower() in (".md", ".markdown")
    elements: list[dict[str, Any]] = [{
        "localKey": FILE_KEY, "label": "TextUnit", "kind": "file", "name": PurePosixPath(path).name,
        "qualifiedName": path, "text": text[:500], "startLine": 1, "endLine": max(len(text.splitlines()), 1),
    }]  # fmt: skip
    sections: list[dict[str, Any]] = []
    trail: list[tuple[int, str, int, dict[str, Any]]] = []  # open headings: level, heading, sibling number, section
    siblings: Counter[tuple[tuple[str, ...], str]] = Counter()
    counts: Counter[tuple[str, str]] = Counter()
    current: dict[str, Any] | None = None
    sentence_index = 0

    for kind, data in _blocks(text) if markdown else _plain_blocks(text):
        if kind == "heading":
            level, pieces, first = data
            heading = _normalize("".join(p for p, _ in pieces))
            while trail and trail[-1][0] >= level:
                trail.pop()
            above = tuple(h for _, h, _, _ in trail)
            siblings[above, section_local_key([heading], [1])] += 1
            number = siblings[above, section_local_key([heading], [1])]
            key = section_local_key([*above, heading], [*(n for _, _, n, _ in trail), number])
            current = _section(path, key, heading, trail[-1][3]["localKey"] if trail else FILE_KEY, first, level)
            trail.append((level, heading, number, current))
            sections.append(current)
            elements.append(current)
            continue
        if current is None:
            current = _section(path, section_local_key([], []), "(preamble)", FILE_KEY, 0, 0)
            sections.append(current)
            elements.append(current)
        for sentence, start, end in _sentences(data):
            key = current["localKey"]
            counts[key, sentence] += 1
            elements.append({
                "localKey": sentence_local_key(key, sentence, counts[key, sentence]), "label": "TextUnit",
                "kind": "sentence", "name": sentence[:NAME_MAX], "qualifiedName": None, "text": sentence,
                "startLine": start + 1, "endLine": end + 1, "parent": key, "sentenceIndex": sentence_index,
            })  # fmt: skip
            sentence_index += 1
    _finish_sections(sections, text.split("\n"))
    return elements


def _finish_sections(sections: list[dict[str, Any]], lines: list[str]) -> None:
    """Fill endLine (up to the next heading of the same or a higher level) and text (own lines until any heading)."""
    total = len(lines) - (0 if lines[-1] else 1)
    for i, s in enumerate(sections):
        later = sections[i + 1 :]
        outer = next((n for n in later if n["_level"] <= s["_level"]), None)
        s["endLine"] = max(outer["_first"] if outer else total, s["startLine"])
        stop = later[0]["_first"] if later else total
        s["text"] = "\n".join(lines[s["_first"] : stop]).strip()[:SECTION_TEXT_MAX]
        del s["_level"], s["_first"]


def render_sentences(elements: list[dict[str, Any]]) -> str:
    """Sentence texts ordered by sentenceIndex, one per line (reference rendering of ADR-0015 S3)."""
    sentences = sorted((e for e in elements if e.get("sentenceIndex") is not None), key=lambda e: e["sentenceIndex"])
    return "".join(e["text"] + "\n" for e in sentences)
