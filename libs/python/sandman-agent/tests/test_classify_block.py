import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from sandman_agent.classify_tree import (
    BEGIN,
    END,
    BlockError,
    apply_block,
    diff,
    parse_block,
    render_block,
    reviewed_rules,
    weak_fingerprint,
    write_atomic,
)

ROOT = Path(__file__).resolve().parents[4]
CASES = json.loads((ROOT / "contracts" / "classification" / "block-cases.json").read_text())["cases"]
BLOCK = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\r?\n?", re.S)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_block_cases(case: dict[str, Any]) -> None:
    existing = case["existing"] or ""
    assert parse_block(existing).status == case["state"]
    block = render_block(case["rules"], case["reviewed"])
    if "error" in case:
        with pytest.raises(BlockError, match=re.escape(case["error"])):
            apply_block(existing, block)
        return
    new = apply_block(existing, block)
    assert new == case["expected"]
    assert apply_block(new, block) == new
    assert parse_block(new).status == "ok"
    assert BLOCK.sub("", new).rstrip() == BLOCK.sub("", existing).rstrip()
    if existing:
        assert new.startswith(existing.split(BEGIN)[0].rstrip("\r\n"))
    assert reviewed_rules(new) == [{"glob": r["glob"], "kind": r["kind"]} for r in case["rules"]]
    assert parse_block(new).reviewed == case["reviewed"]


def test_crlf_is_kept_everywhere() -> None:
    new = apply_block("a: 1\r\nb: 2\r\n", render_block([{"glob": "x", "kind": "code"}], "0" * 8))
    assert "\n" not in new.replace("\r\n", "")


def test_diff_and_fingerprint() -> None:
    assert diff("a\n", "a\nb\n").splitlines()[-1] == "+b"
    assert diff("a\n", "a\n") == ""
    assert weak_fingerprint(["b", "a"]) == weak_fingerprint(["a", "b"]) != weak_fingerprint(["a"])
    assert re.fullmatch(r"[0-9a-f]{8}", weak_fingerprint([]))


def test_write_atomic_replaces_and_leaves_nothing(tmp_path: Path) -> None:
    target = tmp_path / "sandman.yaml"
    write_atomic(target, "a\r\nb\r\n")
    assert target.read_bytes() == b"a\r\nb\r\n"
    target.chmod(0o640)
    write_atomic(target, "new")
    assert target.read_text() == "new"
    assert target.stat().st_mode & 0o777 == 0o640
    assert os.listdir(tmp_path) == ["sandman.yaml"]


def test_write_atomic_failure_keeps_target(tmp_path: Path) -> None:
    target = tmp_path / "sandman.yaml"
    target.write_text("old")
    with pytest.raises(UnicodeEncodeError):
        write_atomic(target, "\ud800")
    assert target.read_text() == "old"
    assert os.listdir(tmp_path) == ["sandman.yaml"]
