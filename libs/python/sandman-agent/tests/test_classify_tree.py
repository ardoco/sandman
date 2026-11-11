import json
import random
from pathlib import PurePosixPath

from sandman_agent.classify import Verdict, resolve
from sandman_agent.classify_tree import build_tree, fold, generate_rules, generate_with_pins, pin_rules

KINDS = ["code", "test", "other", "documentation", "requirements"]


def v(kind: str, weak: bool = False) -> Verdict:
    return Verdict(kind, None, "heuristic", "r", weak)


def test_uniform_folder_collapses_and_opens() -> None:
    tree = build_tree({"src/a.java": v("code"), "src/b/c.java": v("code"), "docs/x.md": v("documentation")})
    rows = fold(tree)
    assert [(r["path"], r["kind"], r["files"], r["expandable"]) for r in rows] == [
        ("docs", "documentation", 1, True),
        ("src", "code", 2, True),
    ]
    opened = fold(tree, {"src"})
    assert [(r["path"], r["depth"]) for r in opened if r["path"].startswith("src")] == [
        ("src", 0),
        ("src/a.java", 1),
        ("src/b", 1),
    ]


def test_mixed_expands_and_weak_propagates() -> None:
    tree = build_tree(
        {"a/x.java": v("code"), "a/y.md": v("other", True), "b/z.java": v("code"), "a/deep/q.py": v("code")}
    )
    rows = fold(tree)
    assert rows[0]["path"] == "a" and rows[0]["kind"] == "mixed" and rows[0]["weak"]
    assert [r["path"] for r in rows if r["depth"] == 1] == ["a/y.md", "a/deep", "a/x.java"]
    assert rows[-1]["path"] == "b" and not rows[-1]["weak"]


def test_tree_is_json_serializable() -> None:
    json.dumps(fold(build_tree({"a/b.py": v("code")}), {"a"}))


def test_deepest_first_and_no_rule_when_heuristic_agrees() -> None:
    files = {"d/a.java": v("code"), "d/e/b.java": v("code"), "d/e/c.java": v("code"), "d/f.md": v("other")}
    rules = generate_rules(files, [{"path": "d", "kind": "test"}, {"path": "d/e", "kind": "other"}])
    assert rules == [{"glob": "d/e/**", "kind": "other"}, {"glob": "d/**", "kind": "test"}]
    assert generate_rules({"d/a.java": v("code")}, [{"path": "d", "kind": "code"}]) == []


def test_file_rules_first_and_redundant_removed() -> None:
    files = {"d/a.java": v("code"), "d/b.java": v("code")}
    rules = generate_rules(files, [{"path": "d/a.java", "kind": "test"}, {"path": "d", "kind": "test"}])
    assert rules == [{"glob": "d/**", "kind": "test"}]
    rules = generate_rules(files, [{"path": "d", "kind": "test"}, {"path": "d/a.java", "kind": "other"}])
    assert rules == [{"glob": "d/a.java", "kind": "other"}, {"glob": "d/**", "kind": "test"}]


def test_generator_property() -> None:
    rnd = random.Random(7)
    dirs = ["a", "a/b", "a/b/c", "x", "x/y", ""]
    for _ in range(300):
        files = {f"{rnd.choice(dirs)}/f{i}.txt".lstrip("/"): v(rnd.choice(KINDS)) for i in range(rnd.randint(1, 12))}
        folders = sorted({str(PurePosixPath(p).parent) for p in files} - {"."})
        edits = [{"path": rnd.choice([*files, *folders]), "kind": rnd.choice(KINDS)} for _ in range(rnd.randint(0, 5))]
        edits = list({e["path"]: e for e in edits}.values())
        expected = {}
        for p, x in files.items():
            hits = [e for e in edits if e["path"] == p or (e["path"] not in files and p.startswith(e["path"] + "/"))]
            best = max(hits, key=lambda e: (e["path"] == p, len(e["path"])), default=None)
            expected[p] = best["kind"] if best else x.kind
        rules = generate_rules(files, edits)
        got = {
            p: next((r["kind"] for r in rules if PurePosixPath(p).full_match(r["glob"])), x.kind)
            for p, x in files.items()
        }
        assert got == expected
        assert {
            p: resolve(p, b"", rules).kind for p in files if any(PurePosixPath(p).full_match(r["glob"]) for r in rules)
        } == {p: k for p, k in expected.items() if any(PurePosixPath(p).full_match(r["glob"]) for r in rules)}


def test_pin_rules_for_changed_kind() -> None:
    files = {"docs/guide.md": v("documentation", True), "src/A.java": v("code")}
    pins = pin_rules(files, {})
    assert [(p["path"], p["kind"]) for p in pins] == [("docs/guide.md", "architecture-doc")]
    assert "pinned" in pins[0]["reason"]
    rules = generate_with_pins(files, [], {})
    assert rules[0]["glob"] == "docs/guide.md" and rules[0]["kind"] == "architecture-doc"
    assert generate_with_pins(files, [{"path": "docs/guide.md", "kind": "documentation"}], {}) == []
