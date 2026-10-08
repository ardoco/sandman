"""Operator pages: workflows (list, detail, cancel), dead events (replay)."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException

from sandman_dashboard.orchestrator import (
    CURSOR,
    PROJECT_ID,
    STATUSES,
    WORKFLOW_ID,
    OrchestratorClient,
    Reply,
    Unreachable,
)

log = logging.getLogger("sandman_dashboard.audit")
UNREACHABLE = "orchestrator unreachable"


def audit(action: str, target: str, status: int | None) -> None:
    log.info(
        "admin action",
        extra={"fields": {"event": "admin_action", "action": action, "target": target, "upstream": status}},
    )


async def attempt(call: Awaitable[Reply]) -> tuple[Reply | None, str | None]:
    try:
        return await call, None
    except Unreachable:
        return None, UNREACHABLE


def register(
    app: FastAPI,
    *,
    orchestrator: OrchestratorClient | None,
    poller: Any,
    session: Any,
    page: Callable[..., HTMLResponse],
    verify_csrf: Callable[[Any, str], None],
    read_form: Callable[[Request], Awaitable[dict[str, str]]],
) -> None:
    Session = session

    async def workflow_list(
        request: Request, ctx: Any, message: str | None = None, error: bool = False
    ) -> HTMLResponse:
        q = request.query_params
        project, status, cursor = (q.get(k) or None for k in ("projectId", "status", "cursor"))
        invalid = (
            (project is not None and not PROJECT_ID.match(project))
            or (status is not None and status not in STATUSES)
            or (cursor is not None and not CURSOR.match(cursor))
        )
        items: list[Any] = []
        next_cursor = None
        dead: list[Any] = []
        if orchestrator is None:
            message, error = "no orchestrator configured", True
        elif invalid:
            message, error = "invalid filter", True
        else:
            reply, problem = await attempt(orchestrator.list_workflows(project, status, cursor))
            dead_reply, dead_problem = await attempt(orchestrator.dead_events())
            problem = problem or dead_problem
            if problem or reply is None or not reply.ok or not isinstance(reply.body, dict):
                message, error = problem or UNREACHABLE, True
            else:
                items, next_cursor = reply.body.get("items") or [], reply.body.get("nextCursor")
            if dead_reply is not None and dead_reply.ok and isinstance(dead_reply.body, dict):
                dead = dead_reply.body.get("items") or []
        return page(
            request, ctx, "workflows.html", items=items, next_cursor=next_cursor, dead=dead, statuses=STATUSES,
            project=project or "", status_filter=status or "", message=message, error=error,
        )  # fmt: skip

    @app.get("/workflows")
    async def workflows_page(request: Request, ctx: Session) -> HTMLResponse:
        return await workflow_list(request, ctx)

    async def workflow_detail(
        request: Request, ctx: Any, workflow_id: str, template: str, message: str | None = None, error: bool = False
    ) -> HTMLResponse:
        if not WORKFLOW_ID.match(workflow_id):
            raise HTTPException(404, "workflow not found")
        workflow = None
        if orchestrator is None:
            message, error = "no orchestrator configured", True
        else:
            reply, problem = await attempt(orchestrator.get_workflow(workflow_id))
            if problem:
                message, error = problem, True
            elif reply is not None and reply.ok and isinstance(reply.body, dict):
                workflow = reply.body
            elif reply is not None:
                message, error = f"workflow {reply.problem}", True
        fragment = template.startswith("_")
        return page(request, ctx, template, workflow=workflow, message=message, error=error, fragment=fragment)

    @app.get("/workflows/{workflow_id}")
    async def workflow_page(request: Request, ctx: Session, workflow_id: str) -> HTMLResponse:
        return await workflow_detail(request, ctx, workflow_id, "workflow.html")

    @app.get("/fragments/workflows/{workflow_id}")
    async def workflow_fragment(request: Request, ctx: Session, workflow_id: str) -> HTMLResponse:
        return await workflow_detail(request, ctx, workflow_id, "_workflow.html")

    @app.post("/workflows/{workflow_id}/cancel")
    async def cancel(request: Request, ctx: Session, workflow_id: str) -> HTMLResponse:
        verify_csrf((await read_form(request)).get("csrf"), ctx.csrf)
        if not WORKFLOW_ID.match(workflow_id):
            raise HTTPException(404, "workflow not found")
        message, error = UNREACHABLE, True
        if orchestrator is not None:
            reply, problem = await attempt(orchestrator.cancel(workflow_id))
            audit("cancel_workflow", workflow_id, reply.status if reply else None)
            if reply is not None:
                message, error = ("cancel requested", False) if reply.ok else (f"cancel failed: {reply.problem}", True)
            else:
                message = problem or UNREACHABLE
        return await workflow_detail(request, ctx, workflow_id, "workflow.html", message, error)

    @app.post("/dead-events/replay")
    async def replay(request: Request, ctx: Session) -> HTMLResponse:
        data = await read_form(request)
        verify_csrf(data.get("csrf"), ctx.csrf)
        event_id = data.get("eventId", "")
        if not event_id or len(event_id) > 200:
            raise HTTPException(400, "invalid event id")
        message, error = UNREACHABLE, True
        if orchestrator is not None:
            reply, problem = await attempt(orchestrator.replay(event_id))
            audit("replay_dead_event", event_id, reply.status if reply else None)
            if reply is not None:
                message, error = ("replay requested", False) if reply.ok else (f"replay failed: {reply.problem}", True)
            else:
                message = problem or UNREACHABLE
        return await workflow_list(request, ctx, message, error)

    def experiment(request: Request, ctx: Any, template: str) -> HTMLResponse:
        entry = poller.entries.get("llm-replay")
        details = entry.details if entry and entry.health == "up" else {}
        return page(request, ctx, template, entry=entry, d=details)

    @app.get("/experiments")
    async def experiments_page(request: Request, ctx: Session) -> HTMLResponse:
        return experiment(request, ctx, "experiments.html")

    @app.get("/fragments/experiments")
    async def experiments_fragment(request: Request, ctx: Session) -> HTMLResponse:
        return experiment(request, ctx, "_experiments.html")
