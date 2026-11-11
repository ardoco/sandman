"""Classification review: stored artifact verdicts as a folded tree, and a stateless rule/block generator (ADR-0016)."""

import json
from collections import Counter
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from sandman_agent import CLASSIFIER_VERSION, RULE_KINDS, Verdict
from sandman_agent.classify_tree import (
    BlockError,
    apply_block,
    build_tree,
    diff,
    generate_rules,
    render_block,
    reviewed_rules,
    weak_fingerprint,
)
from starlette.exceptions import HTTPException

from sandman_dashboard.graph import PROJECT_ID, GraphError, GraphService

MAX_ROWS = 20000
MAX_BODY = 1024 * 1024
MAX_EDITS = 5000
MAX_PATH = 1024
UNKNOWN = "unknown"

ARTIFACTS = (
    "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(:ArtifactVersion {projectId: $p}) "
    "WHERE a.origin IS NULL OR a.origin <> 'derived' "
    "RETURN a.path, a.kind, a.format, a.kindSource, a.kindReason, a.kindWeak, a.classifierVersion ORDER BY a.path"
)
LAST_INGESTION = (
    "MATCH (i:Ingestion {projectId: $p}) WITH i ORDER BY i.seq DESC LIMIT 1 "
    "RETURN i.seq, i.createdAt, i.kindCounts, i.warnings"
)


def verdicts_of(rows: list[list[Any]]) -> tuple[dict[str, Verdict], set[int]]:
    """Absent source, reason or weak flag means unknown: such a verdict is never weak."""
    out: dict[str, Verdict] = {}
    versions: set[int] = set()
    for path, kind, fmt, source, reason, weak, version in rows:
        out[path] = Verdict(kind, fmt, cast(Any, source or UNKNOWN), reason or "", weak is True)
        if isinstance(version, int):
            versions.add(version)
    return out, versions


def register(
    app: FastAPI,
    *,
    graph: GraphService,
    session: Any,
    page: Callable[..., HTMLResponse],
    verify_csrf: Callable[[Any, str], None],
) -> None:
    Session = Annotated[Any, Depends(session)]

    def project_of(value: Any) -> str:
        if not isinstance(value, str) or not PROJECT_ID.match(value):
            raise HTTPException(422, "valid project id required")
        return value

    async def load(project: str) -> tuple[dict[str, Verdict], set[int], bool]:
        rows = await graph.fixed(ARTIFACTS, {"p": project}, max_rows=MAX_ROWS)
        return (*verdicts_of(rows), len(rows) >= MAX_ROWS)

    @app.get("/classification")
    async def classification_page(request: Request, ctx: Session, project: str | None = None) -> HTMLResponse:
        if project is not None:
            project_of(project)
        try:
            projects = await graph.projects()
            message = None
        except GraphError as e:
            projects, message = [], str(e)
        return page(
            request, ctx, "classification.html", projects=projects, selected=project, message=message,
            error=message is not None,
        )  # fmt: skip

    @app.get("/api/classification")
    async def api_classification(ctx: Session, project: str | None = None) -> dict[str, Any]:
        p = project_of(project)
        vs, versions, truncated = await load(p)
        ing = await graph.fixed(LAST_INGESTION, {"p": p})
        ingestion = None
        if ing:
            seq, created, counts, warnings = ing[0]
            ingestion = {
                "seq": seq,
                "createdAt": created,
                "kindCounts": json.loads(counts) if counts else None,
                "warnings": warnings or [],
            }
        return {
            "project": p,
            "kinds": dict(sorted(Counter(v.kind for v in vs.values()).items())),
            "ruleKinds": list(RULE_KINDS),
            "ingestion": ingestion,
            "classifierVersions": sorted(versions),
            "currentClassifierVersion": CLASSIFIER_VERSION,
            "outdated": any(v != CLASSIFIER_VERSION for v in versions),
            "files": len(vs),
            "weak": sum(v.weak for v in vs.values()),
            "truncated": truncated,
            "tree": build_tree(vs),
        }

    def check_edits(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list) or len(value) > MAX_EDITS:
            raise HTTPException(422, f"edits must be a list of at most {MAX_EDITS} entries")
        out = []
        for e in value:
            path, kind = (e.get("path"), e.get("kind")) if isinstance(e, dict) else (None, None)
            if kind not in RULE_KINDS:
                raise HTTPException(422, f"unknown kind; expected one of {', '.join(RULE_KINDS)}")
            if (
                not isinstance(path, str)
                or not path
                or len(path) > MAX_PATH
                or any(ord(c) < 32 for c in path)
                or ".." in PurePosixPath(path).parts
                or path.startswith("/")
            ):
                raise HTTPException(422, "invalid edit path")
            out.append({"path": path.rstrip("/"), "kind": kind})
        return out

    @app.post("/api/classification/rules")
    async def api_rules(request: Request, ctx: Session) -> dict[str, Any]:
        verify_csrf(request.headers.get("x-csrf-token"), ctx.csrf)
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            raise HTTPException(400, "invalid Content-Length") from None
        body = bytearray()
        if declared > MAX_BODY:
            raise HTTPException(413, "request body too large")
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                raise HTTPException(413, "request body too large")
        try:
            data = json.loads(body)
        except ValueError:
            raise HTTPException(400, "invalid JSON") from None
        if not isinstance(data, dict):
            raise HTTPException(422, "JSON object required")
        p = project_of(data.get("project"))
        edits = check_edits(data.get("edits"))
        current = data.get("yaml", "")
        if not isinstance(current, str):
            raise HTTPException(422, "yaml must be text")
        vs, _, _ = await load(p)
        new_rules = generate_rules(vs, edits)
        globs = {r["glob"] for r in new_rules}
        rules = new_rules + [r for r in reviewed_rules(current) if r["glob"] not in globs]
        reviewed = weak_fingerprint(
            path
            for path, v in vs.items()
            if v.weak and not any(PurePosixPath(path).full_match(r["glob"]) for r in rules)
        )
        block = render_block(rules, reviewed)
        try:
            text = apply_block(current, block)
        except BlockError as e:
            raise HTTPException(422, str(e)) from None
        return {"rules": rules, "reviewed": reviewed, "block": block, "diff": diff(current, text), "fileText": text}
