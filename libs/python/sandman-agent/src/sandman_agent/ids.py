"""Deterministic ids, keys and fingerprints (contracts/conformance/vectors.json)."""

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

import rfc8785

SEP = "\x1f"
_WS = " \t\n\r\f\v"  # ASCII whitespace only, identical in Java


def sha256_hex(data: str | bytes) -> str:
    """Hex sha256 of UTF-8 text or bytes."""
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    """prefix + '_' + first 32 hex chars of sha256(parts joined by U+001F)."""
    return f"{prefix}_{sha256_hex(SEP.join(parts))[:32]}"


def canonical_json(value: Any) -> bytes:
    """RFC 8785 (JCS) serialization."""
    return rfc8785.dumps(value)


def _slug(heading: str) -> str:
    text = "".join(c.lower() if "A" <= c <= "Z" else c for c in unicodedata.normalize("NFC", heading))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "section"


def section_local_key(headings: Sequence[str], occurrences: Sequence[int]) -> str:
    """'sec:' + heading slugs from the top; n-th sibling with the same slug gets '~n'."""
    parts = (_slug(h) + (f"~{n}" if n >= 2 else "") for h, n in zip(headings, occurrences, strict=True))
    return "sec:" + "/".join(parts)


def sentence_local_key(section_key: str, text: str, occurrence: int) -> str:
    """Section key + '/' + 12 hex of sha256(normalized text), '~n' for the n-th identical sentence."""
    normalized = re.sub(f"[{_WS}]+", " ", unicodedata.normalize("NFC", text)).strip(_WS)
    return f"{section_key}/{sha256_hex(normalized)[:12]}" + (f"~{occurrence}" if occurrence >= 2 else "")


def normalize_concept_name(name: str) -> str:
    """NFC, casefold, NFC, runs of ASCII whitespace to one space, trimmed (conformance vector conceptName)."""
    folded = unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())
    return re.sub(f"[{_WS}]+", " ", folded).strip(_WS)


def fingerprint(type_: str, detector_id: str, involved_keys: Iterable[str]) -> str:
    """sha256 over type, detector id (no version) and involved keys sorted byte-wise by UTF-8."""
    return sha256_hex(SEP.join([type_, detector_id, *sorted(involved_keys, key=str.encode)]))


def evidence_hash(pairs: Iterable[str]) -> str:
    """sha256 over the distinct 'Element.key=Element.contentHash' pairs sorted byte-wise by UTF-8."""
    return sha256_hex(SEP.join(sorted(set(pairs), key=str.encode)))


def new_run_id() -> str:
    """Random run id."""
    return f"run_{uuid.uuid4().hex}"


def new_workflow_id() -> str:
    """Random workflow id."""
    return f"wf_{uuid.uuid4().hex}"
