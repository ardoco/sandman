"""Deterministic identifier renaming for the contamination probe (experiments/PROTOCOL.md section 6)."""

import hashlib
import re
from collections.abc import Iterable


def build_mapping(names: Iterable[str], seed: int) -> dict[str, str]:
    """One pseudonym per name, derived from seed and name, so equal input gives equal output."""
    return {n: "X" + hashlib.sha256(f"{seed}:{n}".encode()).hexdigest()[:8] for n in sorted(set(names)) if n}


def rename(text: str, mapping: dict[str, str]) -> str:
    """Replace whole identifiers only (longest name first, so a name inside a longer one stays untouched)."""
    if not mapping:
        return text
    pattern = "|".join(re.escape(n) for n in sorted(mapping, key=len, reverse=True))
    return re.sub(rf"(?<![\w$])(?:{pattern})(?![\w$])", lambda m: mapping[m.group(0)], text)
