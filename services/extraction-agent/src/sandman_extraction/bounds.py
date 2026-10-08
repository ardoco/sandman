"""Bounds for untrusted strings and per-file warning lists shared by the built-in extractors."""

import re

MAX_WARNINGS = 100
WARNING_MAX = 300
KIND_MAX = 64
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def add_warnings(target: list[str], found: list[str]) -> None:
    """Extend `target` with at most MAX_WARNINGS entries of WARNING_MAX chars plus one '… N more' entry."""
    target.extend(w[:WARNING_MAX] for w in found[:MAX_WARNINGS])
    if len(found) > MAX_WARNINGS:
        target.append(f"… {len(found) - MAX_WARNINGS} more")


def clean_kind(kind: str) -> str | None:
    """`kind` if it is at most KIND_MAX chars without control characters, else None."""
    return kind if len(kind) <= KIND_MAX and not _CONTROL.search(kind) else None
