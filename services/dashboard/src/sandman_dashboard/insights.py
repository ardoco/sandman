"""Insight pages: projects, history, resources."""

import asyncio
import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException

from sandman_dashboard.alerts import memory_ratio, parse_size
from sandman_dashboard.graph import PROJECT_ID, GraphError
from sandman_dashboard.projects import ProjectStats
from sandman_dashboard.resources import Cadvisor
from sandman_dashboard.sampler import RANGES, history


def register(
    app: FastAPI,
    *,
    graph: Any,
    stats: ProjectStats,
    db: sqlite3.Connection,
    cadvisor: Cadvisor,
    poller: Any,
    session: Any,
    page: Callable[..., HTMLResponse],
) -> None:
    Session = session

    @app.get("/projects")
    async def projects_page(request: Request, ctx: Session) -> HTMLResponse:
        try:
            projects = await graph.projects()
        except GraphError as e:
            return page(request, ctx, "projects.html", rows=[], message=str(e), error=True)
        figures = await stats.many([p["id"] for p in projects])
        rows = [(p, figures.get(p["id"])) for p in projects]
        return page(request, ctx, "projects.html", rows=rows)

    @app.get("/projects/{project_id}")
    async def project_page(request: Request, ctx: Session, project_id: str) -> HTMLResponse:
        if not PROJECT_ID.match(project_id):
            raise HTTPException(404, "project not found")
        try:
            known = {p["id"]: p for p in await graph.projects()}
        except GraphError as e:
            return page(request, ctx, "project.html", project=None, s=None, message=str(e), error=True)
        if project_id not in known:
            raise HTTPException(404, "project not found")
        return page(request, ctx, "project.html", project=known[project_id], s=await stats.get(project_id))

    def chosen(request: Request) -> str:
        value = request.query_params.get("range", "24h")
        if value not in RANGES:
            raise HTTPException(422, "range must be one of " + ", ".join(RANGES))
        return value

    @app.get("/history")
    async def history_page(request: Request, ctx: Session) -> HTMLResponse:
        return page(request, ctx, "history.html", range=chosen(request), ranges=list(RANGES))

    @app.get("/api/history")
    async def api_history(request: Request, ctx: Session) -> dict[str, Any]:
        return await asyncio.to_thread(history, db, chosen(request))

    @app.get("/resources")
    async def resources_page(request: Request, ctx: Session) -> HTMLResponse:
        usage = await cadvisor.usage()
        memgraph = poller.entries.get("memgraph")
        details = memgraph.details if memgraph is not None and memgraph.health == "up" else {}
        memgraph_row = None
        if parse_size(details.get("memory")) is not None:
            memgraph_row = {
                "resident": details["memory"],
                "tracked": details.get("memoryTracked"),
                "limit": details.get("memoryLimit"),
                "ratio": memory_ratio(details),
            }
        return page(request, ctx, "resources.html", usage=usage, memgraph=memgraph_row)
