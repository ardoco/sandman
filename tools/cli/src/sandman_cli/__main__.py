"""sandman CLI against the query-service API (contracts/openapi/query-service.yaml)."""

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import yaml

from sandman_cli.evaluation import stats
from sandman_cli.evaluation.compare import compare
from sandman_cli.evaluation.formats import FORMATS, fetch_rows, read_file, rows_to_links, write_links

DEFAULT_URL = "http://127.0.0.1:8080"
CONFIG = "sandman.yaml"  # optional project-level config in DIR (not ingested)
KINDS = ("requirements", "architecture-doc", "architecture-model", "code", "test", "other")
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
    rules = args.kind_rule + (
        (yaml.safe_load(config.read_text()) or {}).get("kindRules", []) if config.is_file() else []
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


def workflows(http: httpx.Client, args: argparse.Namespace) -> int:
    if args.wait:
        deadline = time.monotonic() + args.timeout
        while (wf := _ok(http.get(f"/v1/workflows/{args.wait}")).json())["status"] not in DONE:
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
