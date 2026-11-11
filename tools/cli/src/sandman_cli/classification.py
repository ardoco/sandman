"""Local classification preview shared by `sandman classify` and `sandman ingest`: ignore, rules, review, block."""

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from sandman_agent import CLASSIFIER_VERSION, RULE_KINDS, Verdict, resolve
from sandman_agent.classify import HEAD_BYTES, legacy_guess
from sandman_agent.classify_tree import (
    BlockState,
    apply_block,
    build_tree,
    diff,
    fold,
    generate_rules,
    parse_block,
    pin_rules,
    render_block,
    reviewed_rules,
    weak_fingerprint,
    write_atomic,
)

CONFIG = "sandman.yaml"  # optional project-level config in DIR (not ingested)
KINDS = RULE_KINDS
FIXED_IGNORES = ("node_modules", "target", "build", "dist", "out", "venv", "__pycache__", "vendor")
ADR_FOLDERS = ("adr", "docs/adr/**", "docs/decisions/**", "**/adr")
ADR_RULES = [
    {"glob": f"{folder}/{name}", "kind": "architecture-doc", "format": "markdown"}
    for folder in ADR_FOLDERS
    for name in ("README.md", "index.md", "template.md")
] + [
    {"glob": glob, "kind": "architecture-doc", "format": "adr"}
    for glob in ("adr/*.md", "docs/adr/**/*.md", "docs/decisions/**/*.md", "**/adr/[0-9][0-9][0-9][0-9]-*.md")
]
UNREVIEWED = "unreviewed"
_UNESCAPE = re.compile(r"\[([*?\[])\]")


def kind_rule(value: str) -> dict[str, str]:
    """--kind-rule 'glob=kind'."""
    glob, _, kind = value.rpartition("=")
    if not glob or kind not in KINDS:
        raise argparse.ArgumentTypeError(f"expected glob=kind with kind in {', '.join(KINDS)}: {value!r}")
    return {"glob": glob, "kind": kind}


def scan(root: Path) -> dict[str, Path]:
    """Relative POSIX path -> file, sorted; skips symlinks, dot-files/-dirs (.git, ...) and sandman.yaml."""
    files = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if (
            p.is_file()
            and not p.is_symlink()
            and rel.as_posix() != CONFIG
            and not any(part.startswith(".") for part in rel.parts)
        ):
            files[rel.as_posix()] = p
    return files


def git_files(root: Path) -> set[str] | None:
    """Tracked and untracked-but-not-ignored files below root; None outside a git work tree."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    return set(r.stdout.decode().split("\0")) if r.returncode == 0 else None


def default_ignored(root: Path, rels: Iterable[str]) -> dict[str, str]:
    """Path -> reason for files below a fixed-list folder or excluded by .gitignore."""
    tracked = git_files(root)
    out = {}
    for rel in rels:
        if folder := next((d for d in PurePosixPath(rel).parts[:-1] if d in FIXED_IGNORES), None):
            out[rel] = f"{folder}/"
        elif tracked is not None and rel not in tracked:
            out[rel] = ".gitignore"
    return out


def _head(p: Path) -> bytes:
    with p.open("rb") as f:
        return f.read(HEAD_BYTES)


def _hit(path: str, rules: Iterable[dict[str, str]]) -> dict[str, str] | None:
    return next((r for r in rules if PurePosixPath(path).full_match(r["glob"])), None)


@dataclass
class Project:
    root: Path
    files: dict[str, Path]
    ignored: dict[str, str]
    text: str
    hand: list[dict[str, str]]
    reviewed: list[dict[str, str]]
    state: BlockState
    heads: dict[str, bytes] = field(default_factory=dict)

    @property
    def rules(self) -> list[dict[str, str]]:
        return self.hand + self.reviewed + ADR_RULES

    def head(self, rel: str) -> bytes:
        if rel not in self.heads:
            self.heads[rel] = _head(self.files[rel])
        return self.heads[rel]

    def verdicts(self, reviewed: list[dict[str, str]] | None = None) -> dict[str, Verdict]:
        """Precedence: hand rules, reviewed rules, ADR rules; unmatched files below an ignored folder are ignored."""
        user = self.hand + (self.reviewed if reviewed is None else reviewed)
        rules = user + ADR_RULES
        out = {}
        for rel in self.files:
            if rel in self.ignored and not _hit(rel, user):
                out[rel] = Verdict("ignored", None, "default", f"ignored: {self.ignored[rel]}")
            else:
                out[rel] = resolve(rel, lambda rel=rel: self.head(rel), rules)  # type: ignore[misc]
        return out


def load_project(root: Path, cli_rules: list[dict[str, str]]) -> Project:
    if not root.is_dir():
        raise SystemExit(f"sandman: {root} is not a directory")
    config = root / CONFIG
    text = config.read_text(encoding="utf-8", newline="") if config.is_file() else ""
    state = parse_block(text)
    if state.status == "broken":
        raise SystemExit(f"sandman: {CONFIG}: {state.message}")
    files = scan(root)
    return Project(
        root,
        files,
        default_ignored(root, files),
        text,
        cli_rules + ((yaml.safe_load(text) or {}).get("kindRules") or []),
        reviewed_rules(text),
        state,
    )


def visible(vs: dict[str, Verdict]) -> dict[str, Verdict]:
    return {p: v for p, v in vs.items() if v.kind != "ignored"}


def weak_paths(vs: dict[str, Verdict]) -> list[str]:
    return [p for p, v in vs.items() if v.weak]


def is_reviewed(proj: Project, vs: dict[str, Verdict]) -> bool:
    weak = weak_paths(vs)
    return not weak or proj.state.reviewed == weak_fingerprint(weak)


def unreviewed_text(weak: list[str]) -> str:
    folders = {PurePosixPath(p).parent.as_posix() for p in weak}
    return f"{len(weak)} files in {len(folders)} folders were not reviewed."


def shadow_warnings(proj: Project, vs: dict[str, Verdict]) -> list[str]:
    out = []
    for r in proj.reviewed:
        hits: Counter[str] = Counter()
        for p in vs:
            if PurePosixPath(p).full_match(r["glob"]) and (h := _hit(p, proj.hand)) and h["kind"] != r["kind"]:
                hits[h["glob"]] += 1
        for glob, n in hits.items():
            out.append(f"hand-written rule '{glob}' shadows generated rule '{r['glob']}' for {n} files")
    return out


def legacy_changed(proj: Project, vs: dict[str, Verdict]) -> list[tuple[str, str, str]]:
    return [
        (p, old, v.kind)
        for p, v in visible(vs).items()
        if v.source != "rule" and (old := legacy_guess(p, proj.head(p))[0]) != v.kind
    ]


def _nodes(tree: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for c in tree["children"] if tree["type"] == "dir" else []:
        out[c["path"]] = c
        out.update(_nodes(c))
    return out


def _leaves(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    if node["type"] == "file":
        yield node
    else:
        for c in node["children"]:
            yield from _leaves(c)


def _how(node: dict[str, Any]) -> tuple[str, str]:
    leaves = list(_leaves(node))
    sources = {leaf["source"] for leaf in leaves}
    reasons = list(dict.fromkeys(leaf["reason"] for leaf in leaves))
    return (next(iter(sources)) if len(sources) == 1 else "mixed"), ", ".join(reasons[:2]) + (
        ", ..." if len(reasons) > 2 else ""
    )


def open_with_parents(paths: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        parts = PurePosixPath(p.strip("/")).parts
        out.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return out


def tree_of(vs: dict[str, Verdict], open_paths: Iterable[str] = (), everything: bool = False) -> tuple[Any, Any, Any]:
    tree = build_tree(visible(vs))
    nodes = _nodes(tree)
    opened = {p for p, n in nodes.items() if n["type"] == "dir"} if everything else open_with_parents(open_paths)
    return tree, nodes, fold(tree, opened)


def render(proj: Project, vs: dict[str, Verdict], rows: list[dict[str, Any]], nodes: dict[str, Any]) -> list[str]:
    ignored = Counter(
        v.reason.removeprefix("ignored: ") if v.source == "default" else "rule"
        for v in vs.values()
        if v.kind == "ignored"
    )
    n_ignored = sum(ignored.values())
    head = f"{proj.root.resolve().name}  {len(vs) - n_ignored} files"
    if n_ignored:
        head += f"   {n_ignored} ignored ({', '.join(sorted(ignored)[:3])})"
    cells = []
    for r in rows:
        node = nodes[r["path"]]
        is_dir = r["type"] == "dir"
        if r["kind"] == "mixed":
            how = f"{len(node['kinds'])} kinds"
        else:
            source, reason = _how(node)
            how = f"{source}  {reason}"
        cells.append(
            ("  " * r["depth"] + r["kind"], "?" if r["weak"] else " ", r["path"] + "/" * is_dir, str(r["files"]), how)
        )
    w = [max([len(c[i]) for c in cells] + [len(h)]) for i, h in ((0, "KIND"), (2, "PATH"), (3, "FILES"))]
    lines = [head + f"   classifier v{CLASSIFIER_VERSION}", ""]
    lines.append(f"  {'KIND':<{w[0]}}    {'PATH':<{w[1]}}  {'FILES':>{w[2]}}  SOURCE")
    lines += [f"  {k:<{w[0]}}  {m} {p:<{w[1]}}  {n:>{w[2]}}  {how}".rstrip() for k, m, p, n, how in cells]
    weak = weak_paths(vs)
    if not weak:
        lines += ["", "Review: nothing to review."]
    elif is_reviewed(proj, vs):
        lines += ["", f"Review: done ({len(weak)} files with '?')."]
    else:
        lines += ["", f"Review: not done ({unreviewed_text(weak)[: -len(' were not reviewed.')]} with '?')."]
    return lines


def is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "q"


def _new_kind() -> str | None:
    print(f"  New kind: {' | '.join(RULE_KINDS)}")
    kind = _ask("  > ")
    if kind not in RULE_KINDS:
        print(f"  Unknown kind {kind!r}.")
        return None
    return kind


def _open(leaves: list[dict[str, Any]], edits: dict[str, dict[str, str]]) -> None:
    width = max(len(leaf["path"]) for leaf in leaves)
    for i, leaf in enumerate(leaves, 1):
        print(f"  {i:>2} {leaf['path']:<{width}}  {edits.get(leaf['path'], leaf)['kind']:<18} {leaf['source']}")
    while True:
        answer = _ask("  Number(s) to change, [b]ack > ")
        if answer in ("", "b", "q"):
            return
        try:
            nums = [int(x) for x in answer.replace(",", " ").split()]
            chosen = [leaves[n - 1] for n in nums if n >= 1]
            if len(chosen) != len(nums):
                raise IndexError
        except (ValueError, IndexError):
            print("  Invalid number.")
            continue
        if kind := _new_kind():
            for leaf in chosen:
                edits[leaf["path"]] = {"path": leaf["path"], "kind": kind}


def review_items(tree: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def walk(n: dict[str, Any]) -> None:
        if n["weak"]:
            if n["type"] == "file" or len(n["kinds"]) == 1:
                items.append(n)
            else:
                for c in n["children"]:
                    walk(c)

    for c in tree["children"]:
        walk(c)
    return items


def interactive(tree: dict[str, Any], edits: dict[str, dict[str, str]]) -> bool:
    """Prompt loop over the weak folders and files; edits are collected in `edits`. True if every item was handled."""
    items = review_items(tree)
    complete = True
    for i, n in enumerate(items, 1):
        leaves = list(_leaves(n))
        kind = next(iter(n["kinds"]))
        path = n["path"] + "/" * (n["type"] == "dir")
        print(f"[{i}/{len(items)}] {path}   {n['files']} files   proposed: {kind}   ({_how(n)[1]})")
        print("      " + "   ".join(leaf["path"] for leaf in leaves[:6]) + (" ..." if len(leaves) > 6 else ""))
        while True:
            c = _ask("  [k]eep  [c]hange  [o]pen  [s]kip  [q]uit and save > ")
            if c == "o":
                _open(leaves, edits)
                continue
            if c == "q":
                return False
            if c == "c":
                if new := _new_kind():
                    edits[n["path"]] = {"path": n["path"], "kind": new}
                    break
                continue
            if c in ("k", "s"):
                complete &= c == "k"
                break
    return complete


def edits_of(rules: list[dict[str, str]], paths: Iterable[str]) -> dict[str, dict[str, str]]:
    """Reviewed rules back to path edits; earlier rules win."""
    out: dict[str, dict[str, str]] = {}
    for r in reversed(rules):
        folder = r["glob"].endswith("/**")
        plain = _UNESCAPE.sub(r"\1", r["glob"][:-3] if folder else r["glob"])
        matched = [plain] if not any(c in plain for c in "*?[") else [p for p in paths if _hit(p, [r])]
        out.update({p: {"path": p, "kind": r["kind"]} for p in matched})
    return out


@dataclass
class Update:
    rules: list[dict[str, str]]
    reviewed: str
    text: str
    diff: str


def build_update(proj: Project, new_edits: dict[str, dict[str, str]], complete: bool, pin: bool) -> Update:
    base = proj.verdicts(reviewed=[])
    edits: dict[str, dict[str, str]] = {}
    if pin:
        movable = {p: v for p, v in visible(base).items() if v.source != "rule"}
        edits.update({e["path"]: e for e in pin_rules(movable, {p: proj.head(p) for p in movable})})
    if proj.state.status != "edited":
        edits.update(edits_of(proj.reviewed, base))
    edits.update(new_edits)
    rules = generate_rules(base, edits.values())
    fingerprint = weak_fingerprint(weak_paths(proj.verdicts(reviewed=rules))) if complete else UNREVIEWED
    text = apply_block(proj.text, render_block(rules, fingerprint))
    return Update(rules, fingerprint, text, diff(proj.text, text))


def write_update(proj: Project, up: Update, force: bool, verbose: bool = True) -> bool:
    if up.text == proj.text:
        if verbose:
            print(f"No changes to {CONFIG}.")
        return False
    if verbose:
        print(f"\nChanges to {proj.root / CONFIG} (managed block only):")
        print(up.diff, end="")
        print("Lines outside the block stay unchanged.")
        print("Caution: the lines between BEGIN and END MANAGED BLOCK are overwritten on every save.")
    if proj.state.status == "edited":
        if not force and not is_tty():
            raise SystemExit(f"sandman: the managed block in {CONFIG} was edited by hand; use --force to overwrite it")
        if verbose:
            print("Warning: the managed block was edited by hand, your edit will be overwritten.")
    if is_tty() and not force and verbose and _ask("Write? [Y/n] ") not in ("", "y", "yes"):
        print("Not written.")
        return False
    write_atomic(proj.root / CONFIG, up.text)
    return True


def review_session(proj: Project, vs: dict[str, Verdict], force: bool = False) -> bool:
    """Interactive review of the weak files, then diff, question and write; True if the file was written."""
    tree = build_tree(visible(vs))
    edits: dict[str, dict[str, str]] = {}
    complete = interactive(tree, edits)
    return write_update(proj, build_update(proj, edits, complete, False), force)


def _file_json(p: str, v: Verdict) -> dict[str, Any]:
    return {"path": p, "kind": v.kind, "format": v.format, "source": v.source, "reason": v.reason, "weak": v.weak}


def classify(args: argparse.Namespace) -> int:
    proj = load_project(Path(args.dir), args.kind_rule)
    vs = proj.verdicts()
    warnings = shadow_warnings(proj, vs)
    for w in warnings:
        print(f"Warning: {w}", file=sys.stderr)
    pin = args.pin_legacy
    if args.interactive and not is_tty():
        raise SystemExit("sandman: -i needs a terminal")
    if args.rules_stdin or args.write or args.interactive or pin:
        new: dict[str, dict[str, str]] = {}
        if args.rules_stdin:
            data = json.load(sys.stdin)
            new = {
                e["path"]: {k: v for k, v in e.items() if k in ("path", "kind", "reason")}
                for e in (data["edits"] if isinstance(data, dict) else data)
            }
            bad = [e for e in new.values() if e.get("kind") not in KINDS]
            if bad:
                raise SystemExit(f"sandman: unknown kind in edits: {bad[0]}")
        complete = True
        if args.interactive:
            complete = interactive(build_tree(visible(vs)), new)
        up = build_update(proj, new, complete, pin)
        if args.rules_stdin:
            written = args.write and write_update(proj, up, args.force, verbose=False)
            print(
                json.dumps(
                    {
                        "rules": up.rules,
                        "reviewed": up.reviewed,
                        "block": render_block(up.rules, up.reviewed),
                        "diff": up.diff,
                        "fileText": up.text,
                        "written": written,
                        "warnings": warnings,
                    },
                    indent=2,
                )
            )
        else:
            write_update(proj, up, args.force)
        return 0
    weak = weak_paths(vs)
    if args.check:
        changed = legacy_changed(proj, vs)
        for p, old, now in changed:
            print(f"kind changed in classifier v{CLASSIFIER_VERSION}: {p} was {old}, is {now}")
        if weak and not is_reviewed(proj, vs):
            print(unreviewed_text(weak))
            return 1
        return 0
    tree, nodes, rows = tree_of(vs, args.open, args.all)
    if args.json:
        ignored = {p: v.reason for p, v in vs.items() if v.kind == "ignored"}
        print(
            json.dumps(
                {
                    "root": str(proj.root),
                    "classifierVersion": CLASSIFIER_VERSION,
                    "fileCount": len(vs) - len(ignored),
                    "kinds": dict(sorted(Counter(v.kind for v in visible(vs).values()).items())),
                    "ignored": ignored,
                    "review": {
                        "weak": len(weak),
                        "reviewed": is_reviewed(proj, vs),
                        "fingerprint": weak_fingerprint(weak),
                        "reviewedFingerprint": proj.state.reviewed,
                    },
                    "rows": rows,
                    "files": [_file_json(p, v) for p, v in visible(vs).items()],
                    "warnings": warnings,
                },
                indent=2,
            )
        )
        return 0
    if args.files:
        for p, v in visible(vs).items():
            print(p, v.kind, v.format or "-", v.source, v.reason, sep="\t")
        return 0
    print("\n".join(render(proj, vs, rows, nodes)))
    if weak and not is_reviewed(proj, vs):
        print(f"Run: sandman classify {args.dir} -i      Expand a folder: --open docs/")
    return 0


def review_gate(proj: Project, args: argparse.Namespace) -> tuple[Project, dict[str, Verdict], int | None]:
    """Section 3.8: returns the (reloaded) project, its verdicts and an exit code if ingestion must stop."""
    vs = proj.verdicts()
    _, nodes, rows = tree_of(vs)
    print("\n".join(render(proj, vs, rows, nodes)), file=sys.stderr)
    for w in shadow_warnings(proj, vs):
        print(f"Warning: {w}", file=sys.stderr)
    weak = weak_paths(vs)
    if args.no_review or not weak or (is_reviewed(proj, vs) and not args.review):
        return proj, vs, None
    print(f"Warning: {unreviewed_text(weak)}", file=sys.stderr)
    if not is_tty():
        print("sandman: no terminal for the review; run `sandman classify DIR -i` or pass --no-review", file=sys.stderr)
        return proj, vs, 2
    choice = "r" if args.review else ""
    while choice not in ("r", "p", "a"):
        choice = _ask("[r]eview now  [p]roceed anyway  [a]bort > ")
    if choice == "a":
        return proj, vs, 1
    if choice == "r":
        review_session(proj, vs)
        proj = load_project(proj.root, args.kind_rule)
        vs = proj.verdicts()
    return proj, vs, None
