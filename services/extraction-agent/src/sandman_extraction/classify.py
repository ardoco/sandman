"""Heuristic artifact classification for files that no kind rule matches."""

import csv
import re
from pathlib import PurePosixPath

HEAD_BYTES = 4096
CODE = {".java", ".kt", ".scala", ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cc",
        ".cs", ".rb", ".php", ".swift"}  # fmt: skip
DOCS = {".md", ".markdown", ".txt", ".pdf", ".docx", ".rst", ".adoc"}
MODELS = {".repository": "pcm-repository", ".uml": "uml", ".xmi": "uml"}
TEST_DIRS = {"test", "tests", "__tests__", "spec"}
REQUIREMENT_NAMES = {"requirement", "requirements", "req", "reqs", "srs", "user-stories", "stories", "features"}
ARCHITECTURE_NAMES = {"architecture", "arch", "design", "sad", "adr", "decisions", "c4"}
DOC_DIRS = {"docs", "doc", "documentation", "wiki"}
TEST_NAME = re.compile(r"(?i)(.*tests?|test_.*|.*_test|.*\.test|.*\.spec)")
REQ_HEADING = re.compile(r"(?m)^#{1,6}[ \t]+[A-Za-z][\w.-]*\d(?:[ \t]|$)")


def _is_test_name(stem: str) -> bool:
    return TEST_NAME.fullmatch(stem) is not None or stem.lower().startswith("test_")


def guess(path: str, head: bytes) -> tuple[str, str | None]:
    """(kind, format) from path segments, suffix and the first bytes of the content; ('other', None) without signal."""
    p = PurePosixPath(path)
    suffix = p.suffix.lower()
    dirs = {d.lower() for d in p.parts[:-1]}
    names = dirs | {p.stem.lower()}
    if suffix in MODELS:
        return "architecture-model", MODELS[suffix]
    if suffix in CODE:
        if dirs & TEST_DIRS or _is_test_name(p.stem):
            return "test", None
        return "code", None
    if suffix == ".csv":
        header = head.decode("utf-8", errors="replace").removeprefix("\ufeff").split("\n", 1)[0]
        cols = {c.strip().lower() for c in next(csv.reader([header]), [])}
        return ("requirements", "csv") if {"id", "text"} <= cols else ("other", None)
    if suffix not in DOCS:
        return "other", None
    if names & REQUIREMENT_NAMES:
        return "requirements", None
    if suffix == ".md" and len(REQ_HEADING.findall(head.decode("utf-8", errors="replace"))) >= 2:
        return "requirements", None
    if names & ARCHITECTURE_NAMES:
        return "architecture-doc", "adr" if suffix == ".md" and dirs & {"adr", "decisions"} else None
    if dirs & DOC_DIRS:
        return "architecture-doc", None
    return "other", None
