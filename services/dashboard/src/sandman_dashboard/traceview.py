"""Trace viewer: artifacts in columns, trace links and findings, read-only (reuses sandman_query.traceview)."""

import re
from collections.abc import Callable, Mapping
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from sandman_query import traceview as tv
from starlette.exceptions import HTTPException

from sandman_dashboard.graph import PROJECT_ID, GraphError, GraphService

ELEMENT_ID = re.compile(r"^el_[0-9a-f]{32}$")


def tx_rows(graph: Any) -> Callable[[str, Mapping[str, Any]], Any]:
    async def rows(cypher: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        columns, raw = await graph.run(cypher, params)
        return [dict(zip(columns, r, strict=True)) for r in raw]

    return rows


def register(app: FastAPI, *, graph: GraphService, session: Any, page: Callable[..., HTMLResponse]) -> None:
    Session = Annotated[Any, Depends(session)]

    def project_of(value: str | None) -> str:
        if not value or not PROJECT_ID.match(value):
            raise HTTPException(422, "valid project id required")
        return value

    @app.get("/traceview")
    async def traceview_page(request: Request, ctx: Session, project: str | None = None) -> HTMLResponse:
        if project is not None:
            project_of(project)
        try:
            projects = await graph.projects()
            message = None
        except GraphError as e:
            projects, message = [], str(e)
        return page(
            request, ctx, "traceview.html", projects=projects, selected=project, message=message,
            error=message is not None,
        )  # fmt: skip

    @app.get("/api/traceview")
    async def api_traceview(ctx: Session, project: str | None = None) -> Any:
        return await tv.trace_view(tx_rows(graph), project_of(project))

    @app.get("/api/traceview/element")
    async def api_traceview_element(ctx: Session, id: str, project: str | None = None) -> Any:
        if not ELEMENT_ID.match(id):
            raise HTTPException(422, "invalid element id")
        found = await tv.element(tx_rows(graph), project_of(project), id)
        if found is None:
            raise HTTPException(404, "element not found")
        return found
