"""Working/idle status for the system overview (ServiceStatus, contracts/openapi/shared.yaml)."""

import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


class StatusTracker:
    """Counts active tasks; a service is 'working' while at least one is active."""

    def __init__(self, service: str, version: str | None = None) -> None:
        self.service = service
        self.version = version or os.environ.get("SANDMAN_VERSION", "dev")
        self.active = 0
        self.last_activity: str | None = None

    def _touch(self) -> None:
        self.last_activity = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    @asynccontextmanager
    async def track(self) -> AsyncIterator[None]:
        """Mark one task as active for the duration of the block."""
        self.active += 1
        self._touch()
        try:
            yield
        finally:
            self.active -= 1
            self._touch()

    def snapshot(self, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """ServiceStatus JSON."""
        status: dict[str, Any] = {
            "service": self.service,
            "version": self.version,
            "state": "working" if self.active > 0 else "idle",
            "activeTasks": self.active,
        }
        if self.last_activity:
            status["lastActivityAt"] = self.last_activity
        if details:
            status["details"] = dict(details)
        return status


def status_route(
    tracker: StatusTracker,
    authenticate: Callable[[str | None], str | None],
    details: Callable[[], Mapping[str, Any]] | None = None,
) -> Route:
    """GET /v1/status; 401 without an accepted token."""

    async def endpoint(request: Request) -> Response:
        if authenticate(request.headers.get("authorization")) is None:
            return JSONResponse({"error": "missing or invalid token"}, status_code=401)
        return JSONResponse(tracker.snapshot(details() if details else None))

    return Route("/v1/status", endpoint, methods=["GET"])
