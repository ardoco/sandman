"""Artifact classification shared by the server and all clients: manifest rules first, then a heuristic."""

import csv
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

CLASSIFIER_VERSION = 1
HEAD_BYTES = 4096
ARTIFACT_KINDS = ("requirements", "architecture-doc", "architecture-model", "documentation", "code", "test", "other")
RULE_KINDS = (*ARTIFACT_KINDS, "ignored")
CODE = {".java", ".kt", ".scala", ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cc",
        ".cs", ".rb", ".php", ".swift"}  # fmt: skip
DOCS = {".md", ".markdown", ".txt", ".pdf", ".docx", ".rst", ".adoc"}
MODELS = {".repository": "pcm-repository", ".uml": "uml", ".xmi": "uml"}
TEST_DIRS = {"test", "tests", "__tests__", "spec"}
REQUIREMENT_NAMES = {"requirement", "requirements", "req", "reqs", "srs", "user-stories", "stories", "features"}
ARCHITECTURE_NAMES = {"architecture", "arch", "design", "sad", "adr", "decisions", "c4"}
DOC_DIRS = {"docs", "doc", "documentation", "wiki"}
DOC_FILE_NAMES = {"readme", "changelog", "contributing", "license", "code_of_conduct"}
TEST_NAME = re.compile(r"(?i)(.*tests?|test_.*|.*_test|.*\.test|.*\.spec)")
REQ_HEADING = re.compile(r"(?m)^#{1,6}[ \t]+[A-Za-z][\w.-]*\d(?:[ \t]|$)")


@dataclass(frozen=True)
class Verdict:
    kind: str
    format: str | None
    source: Literal["rule", "heuristic", "default"]
    reason: str
    weak: bool = False


def _is_test_name(stem: str) -> bool:
    return TEST_NAME.fullmatch(stem) is not None or stem.lower().startswith("test_")


def _hit(p: PurePosixPath, names: set[str]) -> str | None:
    """Reason for the first path segment or file name in `names`, else None."""
    for d in p.parts[:-1]:
        if d.lower() in names:
            return f"path segment '{d.lower()}'"
    return f"file name '{p.stem.lower()}'" if p.stem.lower() in names else None


def heuristic(path: str, head: bytes) -> Verdict:
    """Verdict from path segments, suffix and the first bytes of the content; kind 'other' (default) without signal."""
    p = PurePosixPath(path)
    suffix = p.suffix.lower()
    dirs = {d.lower() for d in p.parts[:-1]}

    def found(kind: str, fmt: str | None, reason: str, weak: bool = False) -> Verdict:
        return Verdict(kind, fmt, "heuristic", reason, weak)

    if suffix in MODELS:
        return found("architecture-model", MODELS[suffix], f"suffix {suffix}")
    if suffix in CODE:
        if hit := next((d for d in p.parts[:-1] if d.lower() in TEST_DIRS), None):
            return found("test", None, f"path segment '{hit.lower()}'")
        if _is_test_name(p.stem):
            return found("test", None, f"file name '{p.stem}'")
        return found("code", None, f"suffix {suffix}")
    if suffix == ".csv":
        header = head.decode("utf-8", errors="replace").removeprefix("\ufeff").split("\n", 1)[0]
        cols = {c.strip().lower() for c in next(csv.reader([header]), [])}
        if {"id", "text"} <= cols:
            return found("requirements", "csv", "csv columns id, text")
        return Verdict("other", None, "default", "no signal", True)
    if suffix not in DOCS and not (suffix == "" and p.stem.lower() in DOC_FILE_NAMES):
        return Verdict("other", None, "default", "no signal", True)
    if reason := _hit(p, REQUIREMENT_NAMES):
        return found("requirements", None, reason)
    if suffix == ".md" and (n := len(REQ_HEADING.findall(head.decode("utf-8", errors="replace")))) >= 2:
        return found("requirements", None, f"{n} REQ headings")
    if reason := _hit(p, ARCHITECTURE_NAMES):
        return found("architecture-doc", "adr" if suffix == ".md" and dirs & {"adr", "decisions"} else None, reason)
    if p.stem.lower() in DOC_FILE_NAMES:
        return found("documentation", None, f"file name '{p.name}'")
    if hit := next((d for d in p.parts[:-1] if d.lower() in DOC_DIRS), None):
        return found("documentation", None, f"path segment '{hit.lower()}'", weak=True)
    return Verdict("other", None, "default", "no signal", True)


KNOWLEDGE_PATH = "sandman-knowledge.yaml"
KNOWLEDGE_FORMAT = "sandman-knowledge"


def resolve(path: str, head: bytes | Callable[[], bytes], rules: Sequence[Mapping[str, str | None]] = ()) -> Verdict:
    """The knowledge file, else the first matching rule (keys glob, kind, format), else the heuristic."""
    if path == KNOWLEDGE_PATH:
        return Verdict("other", KNOWLEDGE_FORMAT, "rule", "knowledge file")
    for rule in rules:
        if PurePosixPath(path).full_match(rule["glob"] or ""):
            return Verdict(rule["kind"] or "other", rule.get("format"), "rule", f"rule {rule['glob']}")
    return heuristic(path, head() if callable(head) else head)


def guess(path: str, head: bytes) -> tuple[str, str | None]:
    """(kind, format) of the heuristic; ('other', None) without signal."""
    v = resolve(path, head)
    return v.kind, v.format


def legacy_guess(path: str, head: bytes) -> tuple[str, str | None]:
    # ponytail: old docs-dir heuristic for detecting kind changes, remove one release after classifier v1
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
