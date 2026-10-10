"""REST API (contracts/openapi/orchestrator.yaml), event consumer and wiring."""

import asyncio
import contextlib
import functools
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import nats
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sandman_agent import (
    A2aClient,
    ContractError,
    Contracts,
    EventConsumer,
    KgClient,
    Settings,
    StatusTracker,
    TokenVerifier,
    ensure_stream,
    require_service,
    trusted_actor,
)
from sandman_agent.events import handle_message
from sandman_agent.telemetry import FASTAPI_TELEMETRY
from starlette.exceptions import HTTPException

from sandman_orchestrator import __version__
from sandman_orchestrator.orchestrator import Conflict, Orchestrator
from sandman_orchestrator.rules import KgGraph, Rules

PROJECT_ID = r"^[a-z0-9][a-z0-9-]{1,62}$"
ACTOR = re.compile(r"^user:[A-Za-z0-9._@-]+$")
TRIGGER_CALLERS = {"query-service"}
ADMIN_CALLERS = TRIGGER_CALLERS | {"dashboard"}  # cancel, replay


class PlannerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str = Field(pattern=PROJECT_ID)
    text: str = Field(min_length=3, max_length=4000)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["full", "extract", "tlr", "detect"]
    projectId: str = Field(pattern=PROJECT_ID)
    params: dict[str, Any] = Field(default_factory=dict)
    force: bool = False


def problem(status: int, title: str, detail: str | None = None, errors: list[str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"type": "about:blank", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status, media_type="application/problem+json")


def create_app(
    orch: Orchestrator,
    verifier: TokenVerifier,
    lifespan: Callable[[FastAPI], contextlib.AbstractAsyncContextManager[None]] | None = None,
    ready: Callable[[], dict[str, str]] = dict,
) -> FastAPI:
    app = FastAPI(title="SANDMAN orchestrator", version=__version__, lifespan=lifespan, telemetry=FASTAPI_TELEMETRY)
    tracker = StatusTracker("orchestrator")
    caller = Depends(require_service(verifier))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, e: HTTPException) -> JSONResponse:
        return problem(e.status_code, str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, e: RequestValidationError) -> JSONResponse:
        return problem(
            400, "invalid request", errors=[f"{'/'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]
        )

    def admin(name: str, allowed: set[str] = ADMIN_CALLERS) -> None:
        if name not in allowed:
            raise HTTPException(403, f"caller {name} may not change workflows")

    def actor(name: str, request: Request) -> str:
        user = trusted_actor(name, request.headers)
        if user is not None and not ACTOR.match(user):
            raise HTTPException(400, "invalid X-Sandman-Actor")
        return user or f"service:{name}"

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        checks = {"sqlite": "ok", **ready()}
        up = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if up else "down", "checks": checks}, 200 if up else 503)

    @app.get("/v1/status")
    async def status(name: str = caller) -> dict[str, Any]:
        counts = orch.counts()
        tracker.active = counts.pop("runningSteps")
        tracker.last_activity = orch.db.execute("SELECT max(updated_at) FROM workflows").fetchone()[0]
        return tracker.snapshot(counts)

    @app.get("/v1/workflows")
    async def list_workflows(
        projectId: Annotated[str | None, Query(pattern=PROJECT_ID)] = None,
        status: Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "BLOCKED"] | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        cursor: Annotated[str | None, Query(pattern=r"^\d{1,9}$")] = None,
        name: str = caller,
    ) -> dict[str, Any]:
        offset = int(cursor or 0)  # ponytail: offset cursor, keyset pagination if lists get long
        items = orch.list_workflows(projectId, status, limit, offset)
        return {"items": items, **({"nextCursor": str(offset + limit)} if len(items) == limit else {})}

    @app.post("/v1/workflows", status_code=202)
    async def create_workflow(body: WorkflowRequest, request: Request, name: str = caller) -> Any:
        admin(name, TRIGGER_CALLERS)
        try:
            wf_id = await orch.trigger(body.type, body.projectId, body.params, actor(name, request), body.force)
        except ContractError as e:
            return problem(400, "invalid skill input", errors=e.messages)
        except ValueError as e:
            return problem(400, "nothing to run", str(e))
        return orch.get_workflow(wf_id)

    @app.get("/v1/workflows/{workflowId}")
    async def get_workflow(workflowId: str, name: str = caller) -> Any:
        return orch.get_workflow(workflowId) or problem(404, "workflow not found")

    @app.post("/v1/workflows/{workflowId}/cancel", status_code=202)
    async def cancel_workflow(workflowId: str, request: Request, name: str = caller) -> Any:
        admin(name)
        actor(name, request)
        try:
            if not await orch.cancel(workflowId):
                return problem(404, "workflow not found")
        except Conflict:
            return problem(409, "workflow has no pending or running steps")
        return orch.get_workflow(workflowId)

    @app.post("/v1/workflows/{workflowId}/retry", status_code=202)
    async def retry_workflow(workflowId: str, request: Request, name: str = caller) -> Any:
        admin(name)
        actor(name, request)
        try:
            if not orch.retry(workflowId):
                return problem(404, "workflow not found")
        except Conflict:
            return problem(409, "workflow is not BLOCKED")
        return orch.get_workflow(workflowId)

    def not_implemented() -> JSONResponse:
        return problem(501, "Not Implemented", "planned operation (M6)")

    @app.post("/v1/requests", status_code=202)
    async def create_request(body: PlannerRequest, request: Request, name: str = caller) -> Any:
        admin(name, TRIGGER_CALLERS)
        actor(name, request)
        return not_implemented()

    @app.post("/v1/workflows/{workflowId}/approval")
    async def decide_approval(workflowId: str, body: ApprovalRequest, request: Request, name: str = caller) -> Any:
        admin(name)
        actor(name, request)
        return not_implemented()

    @app.get("/v1/dead-events")
    async def dead_events(name: str = caller) -> dict[str, Any]:
        return {"items": orch.dead_events()}

    @app.post("/v1/dead-events/{eventId}/replay", status_code=202)
    async def replay(eventId: str, request: Request, name: str = caller) -> Any:
        admin(name)
        actor(name, request)
        if not await orch.replay(eventId):
            return problem(404, "dead event not found")
        return Response(status_code=202)

    return app


async def consume(js: Any, handler: Callable[..., Awaitable[None]]) -> None:
    """Durable 'orchestrator' consumer; the handler sees the delivery count (dead events on the last one)."""
    consumer = EventConsumer(js, "orchestrator")
    sub = await js.pull_subscribe("sandman.>", durable="orchestrator", config=consumer.config())
    while True:
        try:
            msgs = await sub.fetch(batch=10, timeout=2)
        except TimeoutError:
            continue
        for msg in msgs:
            delivered = getattr(msg.metadata, "num_delivered", 1) or 1
            await handle_message(msg, functools.partial(handler, delivered=delivered))


def build(env: dict[str, str] | None = None) -> FastAPI:
    """App from SANDMAN_* settings, SANDMAN_STATE_DB and SANDMAN_AGENTS (name=url,...)."""
    env = dict(os.environ) if env is None else env
    settings = Settings.from_env(env)
    settings.require("kg_uri", "nats_url", "service_token", "config_dir")
    assert settings.kg_uri and settings.nats_url and settings.service_token and settings.config_dir
    nats_url = settings.nats_url
    agents = dict(pair.split("=", 1) for pair in env.get("SANDMAN_AGENTS", "").split(",") if pair)
    kg = KgClient(settings.kg_uri, settings.kg_user, settings.kg_password)
    a2a = A2aClient(settings.service_token)
    contracts = Contracts(settings.contracts_dir)
    orch = Orchestrator(
        env.get("SANDMAN_STATE_DB", "/data/orchestrator.db"),
        Rules.load(Path(settings.config_dir) / "orchestrator" / "rules.yaml", contracts),
        KgGraph(kg),
        a2a,
        agents,
        contracts,
    )
    tasks: list[asyncio.Task[None]] = []

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nc = await nats.connect(nats_url)
        js = nc.jetstream()
        await ensure_stream(js)
        await orch.recover()
        tasks.extend([asyncio.create_task(orch.run()), asyncio.create_task(consume(js, orch.consume))])
        yield
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await nc.close()
        await a2a.close()
        await kg.close()
        orch.close()

    def ready() -> dict[str, str]:
        return {"executor": "ok" if tasks and not tasks[0].done() else "down",
                "nats": "ok" if tasks and not tasks[1].done() else "down"}  # fmt: skip

    return create_app(orch, TokenVerifier(settings.service_verify), lifespan, ready)
