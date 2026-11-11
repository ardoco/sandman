"""Folder tree, rule generator and managed `sandman.yaml` block for the classification review (text only, no YAML)."""

import glob as _glob
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from sandman_agent.classify import CLASSIFIER_VERSION, Verdict, legacy_guess

Rule = dict[str, str]
BEGIN = "# >>> sandman:classification >>> BEGIN MANAGED BLOCK"
END = "# <<< sandman:classification <<< END MANAGED BLOCK"
WARNING = (
    "# Warning: `sandman classify --write` and the dashboard and desktop review overwrite",
    "# everything between BEGIN and END. Do not edit here. Put manual rules in kindRules above.",
)


def build_tree(verdicts: Mapping[str, Verdict]) -> dict[str, Any]:
    """Nested JSON-serializable tree; dirs carry `kinds` counts, `files`, `weak`; children weak-first, then by name."""
    root: dict[str, Any] = {"name": "", "path": "", "type": "dir", "children": {}}
    for path, v in verdicts.items():
        node = root
        parts = PurePosixPath(path).parts
        for i, part in enumerate(parts[:-1]):
            node = node["children"].setdefault(
                part, {"name": part, "path": "/".join(parts[: i + 1]), "type": "dir", "children": {}}
            )
        node["children"][parts[-1]] = {
            "name": parts[-1],
            "path": path,
            "type": "file",
            "kind": v.kind,
            "format": v.format,
            "source": v.source,
            "reason": v.reason,
            "weak": v.weak,
        }
    return _finish(root)


def _finish(node: dict[str, Any]) -> dict[str, Any]:
    if node["type"] == "file":
        node["kinds"] = {node["kind"]: 1}
        node["files"] = 1
        return node
    children = sorted((_finish(c) for c in node["children"].values()), key=lambda c: (not c["weak"], c["name"]))
    kinds: Counter[str] = Counter()
    for c in children:
        kinds.update(c["kinds"])
    node.update(children=children, kinds=dict(sorted(kinds.items())), files=sum(kinds.values()))
    node["weak"] = any(c["weak"] for c in children)
    return node


def fold(tree: dict[str, Any], open_paths: Collection[str] = ()) -> list[dict[str, Any]]:
    """Visible rows: uniform folders stay collapsed unless in `open_paths`, mixed folders are expanded."""
    rows: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        is_dir = node["type"] == "dir"
        kind = next(iter(node["kinds"])) if len(node["kinds"]) == 1 else "mixed"
        rows.append(
            {
                "path": node["path"],
                "name": node["name"],
                "type": node["type"],
                "kind": kind,
                "files": node["files"],
                "weak": node["weak"],
                "source": node.get("source"),
                "reason": node.get("reason"),
                "depth": depth,
                "expandable": is_dir,
            }
        )
        if is_dir and (kind == "mixed" or node["path"] in open_paths):
            for c in node["children"]:
                walk(c, depth + 1)

    for c in tree["children"]:
        walk(c, 0)
    return rows


def _file_glob(path: str) -> str:
    return _glob.escape(path)


def _folder_glob(path: str) -> str:
    return f"{_glob.escape(path)}/**"


def _apply(rules: Sequence[Rule], path: str, base: str) -> str:
    for r in rules:
        if PurePosixPath(path).full_match(r["glob"]):
            return r["kind"]
    return base


def generate_rules(verdicts: Mapping[str, Verdict], edits: Iterable[Mapping[str, str]]) -> list[Rule]:
    """Minimal first-match-wins rules: file rules, then folder rules deepest first, none where the heuristic agrees.

    An edit path is a file if it is in `verdicts`, else a folder. An optional `reason` is kept as the rule reason.
    """
    base = {p: v.kind for p, v in verdicts.items()}
    candidates: list[Rule] = []
    for e in edits:
        is_file = e["path"] in verdicts
        rule = {"glob": _file_glob(e["path"]) if is_file else _folder_glob(e["path"]), "kind": e["kind"]}
        if e.get("reason"):
            rule["reason"] = e["reason"]
        candidates.append(
            {**rule, "_depth": str(0 if is_file else len(PurePosixPath(e["path"]).parts)), "_f": str(is_file)}
        )
    candidates.sort(key=lambda r: (r["_f"] != "True", -int(r["_depth"])))
    seen: dict[str, Rule] = {}
    for c in candidates:
        seen.setdefault(c["glob"], c)
    rules = [{k: v for k, v in c.items() if not k.startswith("_")} for c in seen.values()]
    wanted = {p: _apply(rules, p, b) for p, b in base.items()}
    for r in list(rules):
        rest = [x for x in rules if x is not r]
        if all(_apply(rest, p, b) == wanted[p] for p, b in base.items() if PurePosixPath(p).full_match(r["glob"])):
            rules = rest
    return rules


def pin_rules(
    verdicts: Mapping[str, Verdict], heads: Mapping[str, bytes], version: int = CLASSIFIER_VERSION
) -> list[dict[str, str]]:
    """Edits that keep the old kind for files whose `legacy_guess` kind differs from the new verdict."""
    pins = []
    for path, v in verdicts.items():
        old = legacy_guess(path, heads.get(path, b""))[0]
        if old != v.kind:
            pins.append(
                {"path": path, "kind": old, "reason": f"pinned: was {old}, classifier v{version} gives {v.kind}"}
            )
    return pins


def generate_with_pins(
    verdicts: Mapping[str, Verdict], edits: Iterable[Mapping[str, str]], heads: Mapping[str, bytes]
) -> list[Rule]:
    """Pins first, user edits win on the same path."""
    merged = {p["path"]: p for p in pin_rules(verdicts, heads)}
    merged.update({e["path"]: dict(e) for e in edits})
    return generate_rules(verdicts, merged.values())
