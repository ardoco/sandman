"""Fakes (owner services, KG reader) and OpenAPI response validation for the query-service tests."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import yaml
from fastapi import FastAPI, Request, Response
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from sandman_agent.config import DEFAULT_CONTRACTS_DIR

OPENAPI = DEFAULT_CONTRACTS_DIR / "openapi"
SPEC = yaml.safe_load((OPENAPI / "query-service.yaml").read_text())
TOKENS = {
    "orchestrator": "orch-token",
    "tlr-agent": "tlr-token",
    "plugin:stub": "stub-token",
    "plugin:other": "other-token",
}
INC_ID = "inc_" + "a" * 32
LINK_ID = "tl_" + "a" * 32
OTHER_LINK_ID = "tl_" + "b" * 32
OTHER_INC_ID = "inc_" + "b" * 32
WF_ID = "wf_" + "c" * 32
TS = "2026-10-06T12:00:00Z"


def _retrieve(uri: str) -> Resource[Any]:
    path = DEFAULT_CONTRACTS_DIR / uri.rsplit("/contracts/", 1)[1]
    text = path.read_text(encoding="utf-8")
    contents = yaml.safe_load(text) if path.suffix == ".yaml" else json.loads(text)
    return Resource.from_contents(contents, default_specification=DRAFT202012)


REGISTRY: Registry[Any] = Registry(retrieve=_retrieve)  # type: ignore[call-arg]


def validate(path: str, method: str, status: int, body: Any) -> None:
    """Validate a /v1 response body against the operation's response schema in query-service.yaml."""
    response = SPEC["paths"][path][method]["responses"][str(status)]
    if "$ref" in response:  # shared problem responses
        schema: dict[str, Any] = {"$ref": (OPENAPI / "shared.yaml").as_uri() + "#/components/schemas/Problem"}
    else:
        content = response.get("content", {})
        pointer = "/".join(["#/paths", path.replace("/", "~1"), method, "responses", str(status), "content"])
        media = "application/json" if "application/json" in content else next(iter(content), None)
        if media is None:
            assert body in (None, b"", "")
            return
        schema = {"$ref": (OPENAPI / "query-service.yaml").as_uri() + f"{pointer}/{media.replace('/', '~1')}/schema"}
    Draft202012Validator(schema, registry=REGISTRY).validate(body)


class FakeReader:
    """Stands in for sandman_query.reader.Reader; queries are recorded, rows are canned."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self.timeouts: list[float] = []
        self.projects = [
            {"id": "alpha", "name": "Alpha", "llmPolicy": "local-only", "createdAt": TS},
            {"id": "beta", "name": "Beta", "llmPolicy": "cloud-allowed", "createdAt": TS},
        ]
        self.result: tuple[list[str], list[list[Any]]] = (["n"], [[1]])
        self.gate: asyncio.Event | None = None  # blocks run() until set
        self.link_projects = {LINK_ID: "alpha", OTHER_LINK_ID: "beta"}
        self.link_rows: list[dict[str, Any]] = []

    async def close(self) -> None:
        pass

    async def run(self, cypher: str, params: Any, timeout: float = 10.0, max_rows: int | None = None) -> Any:
        if self.gate is not None:
            await self.gate.wait()
        self.queries.append((cypher, dict(params)))
        self.timeouts.append(timeout)
        if cypher.startswith("MATCH (p:Project {id"):
            return ["llmPolicy"], [[p["llmPolicy"]] for p in self.projects if p["id"] == params["id"]]
        if cypher.startswith("MATCH (p:Project)"):
            return ["p"], [[p] for p in self.projects]
        if cypher.startswith("/* evidence"):
            return [], []
        if cypher.startswith("MATCH (l:TraceLink {id: $id"):
            return ["id"], [[params["id"]]] if self.link_projects.get(params["id"]) == params["p"] else []
        if cypher.startswith("/* console:links"):
            columns = list(self.link_rows[0]) if self.link_rows else []
            return columns, [list(r.values()) for r in self.link_rows]
        if cypher.lstrip().startswith(("MATCH (l:TraceLink", "MATCH (i:Inconsistency", "MATCH (a:Artifact {")):
            return [], []  # exports
        columns, rows = self.result
        return columns, rows[:max_rows]

    async def rows(self, cypher: str, params: Any, timeout: float = 10.0) -> list[dict[str, Any]]:
        columns, rows = await self.run(cypher, params, timeout)
        return [dict(zip(columns, r, strict=True)) for r in rows]


@dataclass
class FakeOwner:
    """One ASGI app standing in for extraction-agent, inconsistency-agent and orchestrator; records calls."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    inconsistencies: dict[str, str] = field(default_factory=lambda: {INC_ID: "alpha", OTHER_INC_ID: "beta"})
    transition_status: int = 200
    link_problem: int | None = None
    force_status: int | None = None  # every response gets this status
    items: list[dict[str, Any]] = field(default_factory=list)
    detail_patch: dict[str, Any] = field(default_factory=dict)
    app: FastAPI = field(init=False)

    def __post_init__(self) -> None:
        self.app = FastAPI()

        @self.app.api_route("/{path:path}", methods=["GET", "POST", "PUT"])
        async def any_route(path: str, request: Request) -> Response:
            body = b"".join([chunk async for chunk in request.stream()])
            self.calls.append({"method": request.method, "path": "/" + path, "headers": dict(request.headers),
                               "query": request.url.query, "body": body})  # fmt: skip
            return self.respond(request.method, "/" + path, body)

    def respond(self, method: str, path: str, body: bytes) -> Response:
        if self.force_status:
            return Response("{}", self.force_status, media_type="application/json")

        def ok(data: Any, status: int = 200) -> Response:
            return Response(json.dumps(data), status, media_type="application/json")

        parts = path.strip("/").split("/")
        if method == "PUT" and parts[:2] == ["v1", "projects"]:
            return ok({"id": parts[2], "name": "N", "llmPolicy": "local-only", "createdAt": TS})
        if path.endswith("/ingestions/check"):
            return ok({"missing": []})
        if path.endswith("/ingestions"):
            return ok({"ingestionId": "ing_" + "1" * 32, "projectId": parts[2], "seq": 1, "mode": "full",
                       "createdVersionIds": [], "reactivatedVersionIds": [], "deletedArtifactIds": [],
                       "unchangedCount": 0, "createdAt": TS}, 201)  # fmt: skip
        if parts[:2] == ["v1", "links"] and method == "POST":
            if self.link_problem:
                return Response(json.dumps({"type": "about:blank", "title": "Conflict", "status": self.link_problem,
                                            "detail": "explicit-reference links are not reviewed"}),
                                self.link_problem, media_type="application/problem+json")  # fmt: skip
            return ok({"id": parts[2], "status": json.loads(body)["status"], "reviewedLinkIds": [parts[2]]})
        if parts[:2] == ["v1", "inconsistencies"]:
            project = self.inconsistencies.get(parts[2])
            if project is None:
                return Response(json.dumps({"type": "about:blank", "title": "Not Found", "status": 404,
                                            "detail": "inconsistency not found"}), 404,
                                media_type="application/problem+json")  # fmt: skip
            if method == "POST":
                if self.transition_status == 409:
                    return Response(json.dumps({"type": "about:blank", "title": "Conflict", "status": 409,
                                                "detail": "status changed concurrently"}), 409,
                                    media_type="application/problem+json")  # fmt: skip
                return ok(detail(parts[2], project, "ACCEPTED"))
            return ok({**detail(parts[2], project), **self.detail_patch})
        if path.endswith("/inconsistencies"):
            return ok({"items": self.items, "counts": {"OPEN": len(self.items)}})
        if parts == ["v1", "workflows"]:
            if method == "POST":
                return ok({**workflow(json.loads(body)["projectId"]), "steps": []}, 202)
            return ok({"items": [workflow("alpha")]})
        if parts[:2] == ["v1", "workflows"]:
            return ok({**workflow("alpha"), "steps": []})
        return Response(status_code=404)


def detail(iid: str, project: str, status: str = "OPEN") -> dict[str, Any]:
    return {"id": iid, "projectId": project, "type": "t", "severity": "info", "title": "T", "status": status,
            "detector": "d", "createdAt": TS, "updatedAt": TS, "description": "d", "involved": [], "history": [],
            "allowedTransitions": ["ACCEPTED"]}  # fmt: skip


def workflow(project: str) -> dict[str, Any]:
    return {"id": WF_ID, "projectId": project, "trigger": {"kind": "manual"}, "status": "RUNNING",
            "createdAt": TS, "updatedAt": TS}  # fmt: skip


def validate_problem(body: Any) -> None:
    schema = {"$ref": (OPENAPI / "shared.yaml").as_uri() + "#/components/schemas/Problem"}
    Draft202012Validator(schema, registry=REGISTRY).validate(body)


def validate_projects(body: Any) -> None:
    schema = {"$ref": (OPENAPI / "query-service.yaml").as_uri() + "#/components/schemas/ProjectWithRole"}
    for item in body:
        Draft202012Validator(schema, registry=REGISTRY).validate(item)


def validate_shared(schema: str, body: Any) -> None:
    ref = {"$ref": (OPENAPI / "shared.yaml").as_uri() + f"#/components/schemas/{schema}"}
    Draft202012Validator(ref, registry=REGISTRY).validate(body)
