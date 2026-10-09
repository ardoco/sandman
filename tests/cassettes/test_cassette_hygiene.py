"""Committed cassettes are valid CassetteEntry lines and carry no bearer tokens or keys."""

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
COMMON = json.loads((ROOT / "contracts/common.schema.json").read_text())
ENTRY = Draft202012Validator({"$defs": COMMON["$defs"], "$ref": "#/$defs/CassetteEntry"})
SECRET = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-[A-Za-z0-9_-]{8,}", re.IGNORECASE)
URL = re.compile(r"https?://(?!json-schema\.org/)")
CASSETTES = sorted([*(ROOT / "tests/cassettes").rglob("*.jsonl"), *ROOT.glob("services/*/tests/cassettes/**/*.jsonl")])


def test_cassettes_are_found() -> None:
    assert CASSETTES


@pytest.mark.parametrize("path", CASSETTES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_cassette_is_valid_and_secret_free(path: Path) -> None:
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert lines
    for n, line in enumerate(lines, 1):
        errors = [e.message for e in ENTRY.iter_errors(json.loads(line))]
        assert not errors, f"{path.name}:{n}: {errors}"
        assert not SECRET.search(line), f"{path.name}:{n}: bearer token or sk- key"
        assert not URL.search(line), f"{path.name}:{n}: URL other than json-schema.org"
