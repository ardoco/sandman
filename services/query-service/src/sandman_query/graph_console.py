"""Console graph explorer (T6.5): project-scoped JSON routes over the shared graph service of sandman_agent.graphview.

Every route needs a console session and a role in the project. Fixed statements are parameterized and anchored to the
project; user statements pass the scoped guard. `Run` and `Ingestion` nodes stay hidden for non-admin users.
"""

import hmac
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from neo4j import AsyncDriver
from sandman_agent.graphview import MAX_NODES, MEMORY_LIMIT, GraphError, GraphService, in_project, schema_names
from starlette.exceptions import HTTPException

from sandman_query.guard import GuardError, check, tokenize
from sandman_query.reader import ScopeViolation, check_scope

if TYPE_CHECKING:
    from sandman_query.console import Ctx
    from sandman_query.service import Service

log = logging.getLogger(__name__)
HIDDEN_LABELS = ("Run", "Ingestion")
STATES = ("current", "historic")
MAX_DEPTH = 3
MAX_PATH_HOPS = 6
MAX_ARTIFACTS = 500
MAX_ARTIFACT_ID = 200
VERSIONED = ["ArtifactVersion", "Element"]
CURRENT = (
    "(:Artifact)-[:CURRENT_VERSION]->({v}) OR (:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->({v})"
)
SAFE_ID = re.compile(r"^[A-Za-z0-9._:#/-]{1,200}$")


def visible(var: str) -> str:
    return f"NOT any(l IN labels({var}) WHERE l IN $hidden)"


def hidden_in(value: Any, hidden: list[str]) -> bool:
    """A returned node map (jsonable form, any depth) carries a hidden label."""
    if isinstance(value, Mapping):
        return bool(set(value.get("_labels", ())) & set(hidden)) or any(hidden_in(v, hidden) for v in value.values())
    if isinstance(value, list):
        return any(hidden_in(v, hidden) for v in value)
    return False


def listed(values: list[str]) -> list[str] | None:
    return [v for v in values if v] or None


class ConsoleGraph(GraphService):
    def __init__(
        self, driver: AsyncDriver, labels: list[str], rel_types: list[str], memory_limit: str = MEMORY_LIMIT
    ) -> None:
        super().__init__(driver, labels, rel_types, guard=check, guard_error=GuardError, memory_limit=memory_limit)

    async def artifacts(self, project: str) -> list[dict[str, str]]:
        rows = await self.fixed(
            "MATCH (a:Artifact {projectId: $project}) RETURN a.id, a.path ORDER BY a.path LIMIT $limit",
            {"project": self._project(project), "limit": MAX_ARTIFACTS},
        )
        return [{"id": r[0], "path": r[1]} for r in rows]

    async def start_nodes(
        self,
        project: str,
        hidden: list[str],
        labels: list[str] | None = None,
        types: list[str] | None = None,
        artifact: str | None = None,
        state: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        wanted, wanted_types = self._filters(labels, types)
        if artifact and not SAFE_ID.match(artifact):
            raise GraphError("invalid artifact id")
        if state and state not in STATES:
            raise GraphError("unknown version state")
        rows = await self.fixed(
            f"MATCH (n) WHERE {in_project('n')} AND {visible('n')} "
            "AND ($labels IS NULL OR any(l IN labels(n) WHERE l IN $labels)) "
            "AND ($q IS NULL OR toLower(n.key) CONTAINS $q OR toLower(n.name) CONTAINS $q "
            "OR toLower(n.id) CONTAINS $q) "
            "AND ($artifact IS NULL OR n.id = $artifact OR n.artifactId = $artifact "
            "OR (:Artifact {id: $artifact})-[:HAS_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(n)) "
            "AND ($state IS NULL OR NOT any(l IN labels(n) WHERE l IN $versioned) "
            f"OR ($state = 'current') = ({CURRENT.format(v='n')})) "
            "RETURN n LIMIT $limit",
            {"project": self._project(project), "hidden": hidden, "labels": wanted,
             "q": (q or "").strip().lower()[:200] or None, "artifact": artifact or None,
             "state": state or None, "versioned": VERSIONED, "limit": MAX_NODES + 1},
        )  # fmt: skip
        return await self._result(rows, MAX_NODES, types=wanted_types)

    async def expand_node(
        self,
        project: str,
        node_id: str,
        hidden: list[str],
        depth: int = 1,
        labels: list[str] | None = None,
        types: list[str] | None = None,
    ) -> dict[str, Any]:
        if not 1 <= depth <= MAX_DEPTH:
            raise GraphError(f"hop depth must be between 1 and {MAX_DEPTH}")
        wanted, wanted_types = self._filters(labels, types)
        rows = await self.fixed(
            f"MATCH (n) WHERE id(n) = $id AND {in_project('n')} AND {visible('n')} "
            f"MATCH p = (n)-[*1..{depth}]-(m) "
            f"WHERE all(x IN nodes(p) WHERE {in_project('x')} AND {visible('x')}) "
            "AND all(r IN relationships(p) WHERE $types IS NULL OR type(r) IN $types) "
            "AND ($labels IS NULL OR all(x IN tail(nodes(p)) WHERE any(l IN labels(x) WHERE l IN $labels))) "
            "RETURN p LIMIT $limit",
            {"id": self._node_id(node_id), "project": self._project(project), "hidden": hidden, "types": wanted_types,
             "labels": wanted, "limit": MAX_NODES + 1},
        )  # fmt: skip
        result = await self._result(rows, MAX_NODES, types=wanted_types)
        result["truncated"] = result["truncated"] or len(rows) > MAX_NODES
        return result

    async def shortest_path(
        self, project: str, source: str, target: str, hidden: list[str], hops: int = MAX_PATH_HOPS
    ) -> dict[str, Any]:
        if not 1 <= hops <= MAX_PATH_HOPS:
            raise GraphError(f"max hops must be between 1 and {MAX_PATH_HOPS}")
        rows = await self.fixed(
            f"MATCH (a) WHERE id(a) = $a AND {in_project('a')} AND {visible('a')} "
            f"MATCH (b) WHERE id(b) = $b AND {in_project('b')} AND {visible('b')} "
            f"MATCH p = (a)-[*BFS ..{hops} (r, x | {in_project('x')} AND {visible('x')})]-(b) RETURN p LIMIT 1",
            {"a": self._node_id(source), "b": self._node_id(target), "project": self._project(project),
             "hidden": hidden},
        )  # fmt: skip
        result = await self._result(rows, MAX_NODES)
        result["found"] = bool(rows)
        return result

    async def query(self, project: str, statement: str, hidden: list[str]) -> dict[str, Any]:
        try:
            names = {t.text for t in tokenize(statement) if t.kind in ("name", "qname")}
        except GuardError as e:
            raise GraphError(str(e)) from None
        if names & set(hidden):
            raise GraphError("the statement names a node type that your role cannot see")
        result = await self.cypher(statement, self._project(project))
        try:
            check_scope(result["rows"], project)
        except ScopeViolation as e:
            raise GraphError(str(e)) from None
        if hidden_in(result["rows"], hidden):
            raise GraphError("the result contains nodes of a type that your role cannot see")
        return result


def mount(
    router: APIRouter, svc: "Service", ctx_dep: Callable[..., "Ctx"], render: Callable[..., HTMLResponse]
) -> None:
    """Adds the graph page and its JSON routes to the console router."""
    driver = getattr(svc.reader, "driver", None)
    labels, rel_types = schema_names(svc.settings.contracts_dir)
    svc.graph = ConsoleGraph(driver, labels, rel_types, svc.runner.memory_limit) if driver else None  # type: ignore[attr-defined]
    Session = Depends(ctx_dep)
    base = "/projects/{project}/graph"

    def access(ctx: "Ctx", project: str) -> list[str]:
        """Hidden labels of the caller; 403 without a role in the project."""
        try:
            svc.check(ctx.who, "queryCypher", project)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            raise HTTPException(403, "no role in this project") from None
        return [] if ctx.who.unrestricted_admin else list(HIDDEN_LABELS)

    def graph() -> ConsoleGraph:
        found = getattr(svc, "graph", None)
        if found is None:
            raise GraphError("knowledge graph unavailable", 503)
        return found  # type: ignore[no-any-return]

    async def respond(work: Callable[[ConsoleGraph], Awaitable[dict[str, Any]]]) -> Response:
        try:
            return JSONResponse(await work(graph()))
        except GraphError as e:
            return JSONResponse({"error": str(e)}, e.status)

    def numbers(value: str | None, default: int) -> int:
        return int(value) if value and value.isdigit() else default

    @router.get(base)
    async def page(project: str, ctx: "Ctx" = Session) -> HTMLResponse:
        hidden = access(ctx, project)
        message, artifacts, shown, rel_types = None, [], [], []
        try:
            g = graph()
            artifacts = await g.artifacts(project)
            shown = [label for label in g.labels if label not in hidden]
            rel_types = g.rel_types
        except GraphError as e:
            message = str(e)
        return render(ctx, "graph.html", project=project, labels=shown, rel_types=rel_types, artifacts=artifacts,
                      states=STATES, message=message, error=message is not None)  # fmt: skip

    @router.get(base + "/start")
    async def start(project: str, request: Request, ctx: "Ctx" = Session) -> Response:
        hidden, p = access(ctx, project), request.query_params
        return await respond(
            lambda g: g.start_nodes(
                project,
                hidden,
                listed(p.getlist("labels")),
                listed(p.getlist("types")),
                p.get("artifact"),
                p.get("state"),
                p.get("q"),
            )  # fmt: skip
        )

    @router.get(base + "/expand")
    async def expand(project: str, request: Request, ctx: "Ctx" = Session) -> Response:
        hidden, p = access(ctx, project), request.query_params
        return await respond(
            lambda g: g.expand_node(
                project,
                p.get("id", ""),
                hidden,
                numbers(p.get("hops"), 1),
                listed(p.getlist("labels")),
                listed(p.getlist("types")),
            )  # fmt: skip
        )

    @router.get(base + "/path")
    async def path(project: str, request: Request, ctx: "Ctx" = Session) -> Response:
        hidden, p = access(ctx, project), request.query_params
        return await respond(
            lambda g: g.shortest_path(
                project, p.get("source", ""), p.get("target", ""), hidden, numbers(p.get("hops"), MAX_PATH_HOPS)
            )  # fmt: skip
        )

    @router.post(base + "/query")
    async def query(project: str, request: Request, ctx: "Ctx" = Session) -> Response:
        hidden = access(ctx, project)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), ctx.csrf.encode()):
            raise HTTPException(403, "missing or invalid CSRF token")
        try:
            body = await request.json()
        except ValueError:
            body = None
        statement = body.get("cypher") if isinstance(body, dict) else None
        if not isinstance(statement, str) or not statement.strip() or len(statement) > 20000:
            return JSONResponse({"error": "send a JSON body with a non-empty cypher string"}, 400)
        started, ok = time.monotonic(), False
        try:
            response = await respond(lambda g: g.query(project, statement, hidden))
            ok = response.status_code == 200
            return response
        finally:
            log.info(
                "graph query",
                extra={"fields": {"event": "graph_query", "user": ctx.username, "projectId": project,
                                  "durationMs": round((time.monotonic() - started) * 1000), "ok": ok}},
            )  # fmt: skip
