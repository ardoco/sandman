"""sandman CLI against the query-service API (contracts/openapi/query-service.yaml)."""

import argparse
import hashlib
import io
import json
import os
import re
import statistics
import sys
import tarfile
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import yaml

from sandman_cli.evaluation import labeling, nlquery, removal, stats
from sandman_cli.evaluation.compare import compare, identical
from sandman_cli.evaluation.formats import FORMATS, fetch_rows, read_file, rows_to_links, write_links
from sandman_cli.evaluation.perturb import build_mapping, rename

DEFAULT_URL = "http://127.0.0.1:8080"
CONFIG = "sandman.yaml"  # optional project-level config in DIR (not ingested)
KINDS = ("requirements", "architecture-doc", "architecture-model", "code", "test", "other")
ADR_FOLDERS = ("adr", "docs/adr/**", "docs/decisions/**", "**/adr")
ADR_RULES = [
    {"glob": f"{folder}/{name}", "kind": "architecture-doc", "format": "markdown"}
    for folder in ADR_FOLDERS
    for name in ("README.md", "index.md", "template.md")
] + [
    {"glob": glob, "kind": "architecture-doc", "format": "adr"}
    for glob in ("adr/*.md", "docs/adr/**/*.md", "docs/decisions/**/*.md", "**/adr/[0-9][0-9][0-9][0-9]-*.md")
]
DONE = {"SUCCEEDED", "FAILED", "CANCELED", "BLOCKED"}  # BLOCKED waits for a user action


def _pattern(regex: str, what: str) -> Any:
    def check(value: str) -> str:
        if not re.fullmatch(regex, value):
            raise argparse.ArgumentTypeError(f"invalid {what} {value!r}")
        return value

    return check


def kind_rule(value: str) -> dict[str, str]:
    """--kind-rule 'glob=kind'."""
    glob, _, kind = value.rpartition("=")
    if not glob or kind not in KINDS:
        raise argparse.ArgumentTypeError(f"expected glob=kind with kind in {', '.join(KINDS)}: {value!r}")
    return {"glob": glob, "kind": kind}


def kind_of(path: str, rules: Sequence[dict[str, str]]) -> str:
    """First matching glob decides; unmatched files are 'other' (the extraction-agent's rule)."""
    return next((r["kind"] for r in rules if PurePosixPath(path).full_match(r["glob"])), "other")


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


def build_manifest(
    files: dict[str, Path], mode: str, revision: str | None, rules: list[dict[str, str]]
) -> dict[str, Any]:
    manifest: dict[str, Any] = {"mode": mode, "kindRules": rules, "files": []}
    if revision:
        manifest["sourceRevision"] = revision
    for rel, p in files.items():
        with p.open("rb") as f:
            manifest["files"].append({"path": rel, "sha256": hashlib.file_digest(f, "sha256").hexdigest()})
    return manifest


def build_archive(files: dict[str, Path], paths: Sequence[str]) -> bytes:
    # ponytail: archive built in memory, stream via a temp file if snapshots grow beyond a few hundred MB
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in paths:
            tar.add(files[rel], arcname=rel)
    return buf.getvalue()


def _ok(resp: httpx.Response) -> httpx.Response:
    if resp.is_error:
        raise SystemExit(f"sandman: {resp.request.method} {resp.request.url.path}: {resp.status_code} {resp.text}")
    return resp


def ingest(http: httpx.Client, args: argparse.Namespace) -> int:
    root = Path(args.dir)
    files = scan(root)
    if not files:
        raise SystemExit(f"sandman: no files in {root}")
    config = root / CONFIG
    rules = (
        args.kind_rule
        + ((yaml.safe_load(config.read_text()) or {}).get("kindRules", []) if config.is_file() else [])
        + ADR_RULES
    )
    manifest = build_manifest(files, args.mode, args.revision, rules)
    kinds = Counter(kind_of(rel, rules) for rel in files)
    print(f"{len(files)} files: " + ", ".join(f"{k}={n}" for k, n in sorted(kinds.items())), file=sys.stderr)

    base = f"/v1/projects/{args.project}/ingestions"
    missing = set(_ok(http.post(base + "/check", json=manifest)).json()["missing"])
    upload = [f["path"] for f in manifest["files"] if f["sha256"] in missing]
    print(f"uploading {len(upload)} of {len(files)} files", file=sys.stderr)
    parts: dict[str, Any] = {"manifest": (None, json.dumps(manifest), "application/json")}
    if upload:
        parts["archive"] = ("archive.tar.gz", build_archive(files, upload), "application/gzip")
    print(json.dumps(_ok(http.post(base, files=parts)).json(), indent=2))
    return 0


def _transitions(wf: dict[str, Any], seen: dict[str, str], started: float) -> None:
    steps = wf.get("steps", [])
    for i, st in enumerate(steps, 1):
        state = (
            f"{st['status']} attempt {st['attempts']}"
            if st["status"] in ("RUNNING", "PENDING") and st["attempts"]
            else st["status"]
        )
        if seen.get(st["id"]) != state and (state != "PENDING" or st["id"] in seen):
            print(
                f"[{time.monotonic() - started:5.0f}s] step {i}/{len(steps)} {st['skill']} ({st['agent']}): {state}",
                file=sys.stderr,
            )
        seen[st["id"]] = state
        text = (st.get("activity") or {}).get("text", "") if st["status"] == "RUNNING" else ""
        if text and seen.get(st["id"] + "#activity") != text:
            print(f"[{time.monotonic() - started:5.0f}s] step {i}/{len(steps)} {st['skill']}: {text}", file=sys.stderr)
        seen[st["id"] + "#activity"] = text
    if seen.get("") != wf["status"]:
        print(f"[{time.monotonic() - started:5.0f}s] workflow {wf['status']}", file=sys.stderr)
        seen[""] = wf["status"]


def workflows(http: httpx.Client, args: argparse.Namespace) -> int:
    if args.wait:
        started = time.monotonic()
        deadline, seen = started + args.timeout, {}
        while True:
            wf = _ok(http.get(f"/v1/workflows/{args.wait}")).json()
            _transitions(wf, seen, started)
            if wf["status"] in DONE:
                break
            if time.monotonic() > deadline:
                raise SystemExit(f"sandman: timeout waiting for {args.wait} (status {wf['status']})")
            time.sleep(args.poll)
        print(json.dumps(wf, indent=2))
        return 0 if wf["status"] == "SUCCEEDED" else 1
    # ponytail: first page only, follow nextCursor when projects keep many workflows
    for w in _ok(http.get(f"/v1/projects/{args.project}/workflows")).json()["items"]:
        print(w["id"], w["status"], w["trigger"]["kind"], w["createdAt"], sep="\t")
    return 0


def export(http: httpx.Client, args: argparse.Namespace) -> int:
    if args.format in FORMATS:
        if args.what != "tracelinks":
            raise SystemExit(f"sandman: format {args.format} exists for tracelinks only")
        rows = fetch_rows(
            lambda body: _ok(http.post("/v1/query/cypher", json=body)).json(), args.project, args.approach
        )
        sys.stdout.write(write_links(args.format, rows_to_links(args.format, rows, args.code_root)))
        return 0
    params = {"format": args.format} if args.format else {}
    sys.stdout.write(_ok(http.get(f"/v1/projects/{args.project}/export/{args.what}", params=params)).text)
    return 0


def eval_compare(args: argparse.Namespace) -> int:
    fmt = args.format
    report = compare(
        read_file(fmt, args.gold),
        read_file(fmt, args.a),
        {str(p): read_file(fmt, p) for p in args.b},
        args.published,
        args.resamples,
        args.seed,
    )
    print(json.dumps(report, indent=2))
    return 0


def eval_equal(args: argparse.Namespace) -> int:
    result = identical(read_file(args.format, args.expected), read_file(args.format, args.actual))
    print(json.dumps(result, indent=2))
    return 0 if result["equal"] else 1


def eval_pilot(args: argparse.Namespace) -> int:
    runs = args.f1
    sd = statistics.stdev(runs)
    n, capped = stats.runs_needed(sd, len(runs), args.half_width)
    print(json.dumps({"pilotRuns": len(runs), "mean": statistics.fmean(runs), "sd": sd, "runs": n, "capped": capped}))
    return 0


def eval_contamination(args: argparse.Namespace) -> int:
    print(json.dumps(stats.contamination_verdict(args.original, args.perturbed), indent=2))
    return 0


def eval_perturb(args: argparse.Namespace) -> int:
    names = [n.strip() for n in args.names.read_text(encoding="utf-8").splitlines() if n.strip()]
    mapping = build_mapping(names, args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    for path in args.paths:
        (args.out / path.name).write_text(rename(path.read_text(encoding="utf-8"), mapping), encoding="utf-8")
    (args.out / "mapping.json").write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")
    return 0


def eval_removal(args: argparse.Namespace) -> int:
    gold, pcm, text = removal.locate(args.benchmark, args.project)
    acm = args.acm or Path.home() / ".cache/sandman/benchmark-code" / f"{args.project}-code-model.acm"
    links = args.links or Path("experiments/removal") / f"{args.project}.links.csv"
    if args.mode == "links":
        if not args.cmd:
            raise SystemExit("sandman: --cmd is required for mode links")
        removal.baseline_links(args.cmd, text, acm, pcm, links)
        return 0
    if args.mode == "full" and not args.cmd:
        raise SystemExit("sandman: --cmd is required for mode full")
    if not links.exists():
        raise SystemExit(f"sandman: {links} not found; run --mode links first")
    result = removal.evaluate(
        gold, pcm, acm, links, mode=args.mode, seed=args.seed, resamples=args.resamples, cmd=args.cmd, text=text
    )
    result["project"] = args.project
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True, default=sorted) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("project", "mode", "runs", "simulated")}, indent=2, default=sorted))
    return 0


def eval_label_sample(args: argparse.Namespace) -> int:
    findings = labeling.load_exports(p.read_text(encoding="utf-8") for p in args.export)
    sheets, key = labeling.sample(findings, args.per_stratum, args.seed, args.detector or labeling.DETECTORS)
    key["exports"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in args.export}
    args.out.mkdir(parents=True, exist_ok=True)
    for r, rows in sheets.items():
        (args.out / f"rater-{r}.csv").write_text(labeling.write_sheet(rows), encoding="utf-8")
    (args.out / "key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    print(json.dumps({"items": len(key["items"]), "strata": key["strata"]}, indent=2))
    return 0


def eval_label_score(args: argparse.Namespace) -> int:
    key = json.loads(args.key.read_text(encoding="utf-8"))
    try:
        a, b = (labeling.read_sheet(p.read_text(encoding="utf-8"), key["items"]) for p in (args.a, args.b))
        adj = args.adjudicated and labeling.read_sheet(args.adjudicated.read_text(encoding="utf-8"), key["items"], True)
    except ValueError as e:
        raise SystemExit(f"sandman: {e}") from e
    if args.disagreements:
        args.disagreements.write_text(labeling.disagreement_sheet(a, b), encoding="utf-8")
    print(json.dumps(labeling.score(key, a, b, adj or None, args.resamples, args.seed), indent=2))
    return 0


MARKDOWN_ROWS = 50


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def impact_markdown(doc: dict[str, Any], console_url: str) -> str:
    out = [f"## Change impact of ingestion {doc['seq']}", ""]
    if not doc["complete"]:
        out += [
            f"Warning: the analysis is not complete (workflow status {doc['workflowStatus']}). Links may be missing.",
            "",
        ]
    out += [f"Warning: {w}" for w in doc["warnings"]]
    if doc["truncated"]:
        out.append("The lists are truncated.")
    out += [f"Changed elements: {len(doc['changed'])}. Affected elements: {len(doc['affected'])}.", ""]
    if doc["affected"]:
        out += ["| Element | Artifact | Distance | Path |", "| --- | --- | --- | --- |"]
        for a in doc["affected"][:MARKDOWN_ROWS]:
            path = " > ".join(step["key"] for step in a["paths"][0])
            out.append(f"| {_cell(a['key'])} | {_cell(a['artifactPath'])} | {a['distance']} | {_cell(path)} |")
        if len(doc["affected"]) > MARKDOWN_ROWS:
            out.append(f"\n{len(doc['affected']) - MARKDOWN_ROWS} more affected elements are not listed.")
    out += ["", f"[Open the impact report in the console]({console_url})", ""]
    return "\n".join(out)


def impact(http: httpx.Client, args: argparse.Namespace) -> int:
    path = f"/v1/projects/{args.project}/ingestions/{args.ingestion}/impact"
    doc = _ok(http.get(path, params={"depth": args.depth})).json()
    if args.format == "json":
        print(json.dumps(doc, indent=2))
    else:
        console = f"{str(http.base_url).rstrip('/')}/console/projects/{args.project}/ingestions/{args.ingestion}/impact"
        sys.stdout.write(impact_markdown(doc, console))
    return 0


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", required=True, type=_pattern(r"[a-z0-9][a-z0-9-]{1,62}", "project id"))
    common.add_argument("--token-file", type=Path, help="personal API token (default: $SANDMAN_TOKEN)")
    p = argparse.ArgumentParser(prog="sandman", description=f"Base URL from $SANDMAN_URL (default {DEFAULT_URL}).")
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("ingest", parents=[common], help="upload a project snapshot (full) or changed files (partial)")
    s.add_argument("--mode", choices=["full", "partial"], required=True)
    s.add_argument("--revision", help="source revision, e.g. Git commit SHA")
    s.add_argument("--kind-rule", type=kind_rule, action="append", default=[], help="glob=kind, before sandman.yaml")
    s.add_argument("dir")
    s.set_defaults(func=ingest)

    s = sub.add_parser("workflows", parents=[common], help="list workflows or wait for one")
    s.add_argument("--wait", metavar="ID", type=_pattern(r"wf_[0-9a-f]{32}", "workflow id"))
    s.add_argument("--timeout", type=float, default=600.0, help="seconds (--wait)")
    s.add_argument("--poll", type=float, default=2.0, help="seconds between polls (--wait)")
    s.set_defaults(func=workflows)

    s = sub.add_parser("export", parents=[common], help="export trace links or inconsistencies")
    s.add_argument("what", choices=["tracelinks", "inconsistencies"])
    s.add_argument("--format", choices=["csv", "json", *FORMATS], help="benchmark formats: tracelinks only (T2.3)")
    s.add_argument("--approach", help="benchmark formats: only links of this approach")
    s.add_argument("--code-root", default="", help="ardoco-sad-code: path prefix removed from code paths")
    s.set_defaults(func=export)

    s = sub.add_parser("impact", parents=[common], help="changed and affected elements of an ingestion")
    s.add_argument("ingestion", type=_pattern(r"ing_[0-9a-f]{32}", "ingestion id"))
    s.add_argument("--depth", type=int, choices=range(1, 5), default=2)
    s.add_argument("--format", choices=["json", "markdown"], default="json")
    s.set_defaults(func=impact)

    s = sub.add_parser("eval", help="evaluation harness (experiments/PROTOCOL.md)")
    ev = s.add_subparsers(required=True)
    c = ev.add_parser("compare", help="metrics with CIs; differences, McNemar, Holm against other systems")
    c.add_argument("--format", choices=list(FORMATS), required=True)
    c.add_argument("--gold", type=Path, required=True)
    c.add_argument("--a", type=Path, required=True, help="exported links of system A")
    c.add_argument("--b", type=Path, action="append", default=[], help="exported links of another system (repeatable)")
    c.add_argument("--published", type=float, help="published F1 of A: report delta with CI")
    c.add_argument("--seed", type=int, default=stats.SEED)
    c.add_argument("--resamples", type=int, default=stats.RESAMPLES)
    c.set_defaults(func=eval_compare, offline=True)

    e = ev.add_parser("equal", help="exact equality of two link sets (exit 1 if they differ)")
    e.add_argument("--format", choices=list(FORMATS), required=True)
    e.add_argument("--expected", type=Path, required=True)
    e.add_argument("--actual", type=Path, required=True)
    e.set_defaults(func=eval_equal, offline=True)

    e = ev.add_parser("pilot", help="run count from pilot F1 values (protocol section 5)")
    e.add_argument("--f1", type=float, action="append", required=True, help="F1 of one pilot run (repeat, at least 2)")
    e.add_argument("--half-width", type=float, default=0.02)
    e.set_defaults(func=eval_pilot, offline=True)

    e = ev.add_parser("contamination", help="memorization verdict (protocol section 6)")
    e.add_argument("--original", type=float, nargs="+", required=True)
    e.add_argument("--perturbed", type=float, nargs="+", required=True)
    e.set_defaults(func=eval_contamination, offline=True)

    e = ev.add_parser("perturb", help="rename identifiers consistently in text files")
    e.add_argument("--names", type=Path, required=True, help="file with one identifier per line")
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--seed", type=int, default=stats.SEED)
    e.add_argument("paths", type=Path, nargs="+")
    e.set_defaults(func=eval_perturb, offline=True)

    e = ev.add_parser("removal", help="removal-protocol evaluation of the model-code detector (T7.3)")
    e.add_argument("--project", choices=list(removal.PCM), required=True)
    e.add_argument("--mode", choices=["simulated", "full", "links"], default="simulated")
    e.add_argument("--benchmark", type=Path, required=True, help="checkout of ArDoCo/Benchmark")
    e.add_argument("--acm", type=Path, help="code model (default: cache of the benchmark code snapshots)")
    e.add_argument("--links", type=Path, help="baseline links, format ardoco-sam-code")
    e.add_argument("--cmd", help="TLR command: gets a directory of jobs (text.txt, code.acm, model.repository)")
    e.add_argument("--seed", type=int, default=42)
    e.add_argument("--resamples", type=int, default=stats.RESAMPLES)
    e.add_argument("--out", type=Path, required=True)
    e.set_defaults(func=eval_removal, offline=True)

    e = ev.add_parser("label-sample", help="stratified blinded rater sheets from finding exports (T3.6)")
    e.add_argument("--export", type=Path, action="append", required=True, help="export inconsistencies --format json")
    e.add_argument("--detector", action="append", help=f"repeatable (default: {', '.join(labeling.DETECTORS)})")
    e.add_argument("--per-stratum", type=int, default=30)
    e.add_argument("--seed", type=int, default=stats.SEED)
    e.add_argument("--out", type=Path, required=True, help="writes rater-a.csv, rater-b.csv, key.json")
    e.set_defaults(func=eval_label_sample, offline=True)

    e = ev.add_parser("label-score", help="kappa, disagreements and precision per stratum (T3.6)")
    e.add_argument("--key", type=Path, required=True)
    e.add_argument("--a", type=Path, required=True, help="labeled sheet of rater a")
    e.add_argument("--b", type=Path, required=True, help="labeled sheet of rater b")
    e.add_argument("--adjudicated", type=Path, help="filled disagreement sheet (item, label)")
    e.add_argument("--disagreements", type=Path, help="write the adjudication sheet here")
    e.add_argument("--seed", type=int, default=stats.SEED)
    e.add_argument("--resamples", type=int, default=stats.RESAMPLES)
    e.set_defaults(func=eval_label_score, offline=True)

    nlquery.register(ev)
    return p


def main(argv: Sequence[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    if getattr(args, "offline", False):
        return int(args.func(args))
    token = args.token_file.read_text().strip() if args.token_file else os.environ.get("SANDMAN_TOKEN", "")
    if not token:
        p.error("no token: set SANDMAN_TOKEN or pass --token-file")
    base_url = os.environ.get("SANDMAN_URL") or DEFAULT_URL
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base_url, headers=headers, transport=transport, timeout=300) as http:
        try:
            result: int = args.func(http, args)
        except httpx.HTTPError as e:
            raise SystemExit(f"sandman: {e}") from e
    return result


if __name__ == "__main__":
    sys.exit(main())
