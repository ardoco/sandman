"""Orchestrator REST client of the dashboard: read workflows and dead events, cancel and replay.

The dashboard token is the only credential; error bodies of the orchestrator are never passed on.
"""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

TIMEOUT = 5.0
PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
WORKFLOW_ID = re.compile(r"^wf_[0-9a-f]{32}$")
CURSOR = re.compile(r"^\d{1,9}$")
STATUSES = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED", "BLOCKED")
ACTIVE = ("PENDING", "RUNNING")
CANCELABLE = ("PENDING", "RUNNING", "BLOCKED")
PROBLEMS = {404: "not found", 409: "workflow has no pending or running steps"}


class Unreachable(Exception):
    """Orchestrator unreachable, timed out, answered 5xx or an unusable body."""


@dataclass(frozen=True)
class Reply:
    status: int
    body: Any = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def problem(self) -> str:
        return PROBLEMS.get(self.status, f"the orchestrator rejected the request ({self.status})")


class OrchestratorClient:
    def __init__(self, url: str, token: str, client: httpx.AsyncClient) -> None:
        self.url, self.client, self.headers = url.rstrip("/"), client, {"Authorization": f"Bearer {token}"}

    async def _call(self, method: str, path: str, params: dict[str, Any] | None = None) -> Reply:
        try:
            resp = await self.client.request(
                method, f"{self.url}{path}", params=params, headers=self.headers, timeout=TIMEOUT
            )
        except httpx.HTTPError as e:
            raise Unreachable from e
        if resp.status_code >= 500:
            raise Unreachable
        if resp.status_code >= 400 or not resp.content:
            return Reply(resp.status_code)
        try:
            return Reply(resp.status_code, resp.json())
        except ValueError as e:
            raise Unreachable from e

    async def list_workflows(
        self, project: str | None = None, status: str | None = None, cursor: str | None = None, limit: int = 50
    ) -> Reply:
        params: dict[str, Any] = {"limit": limit}
        params |= {k: v for k, v in (("projectId", project), ("status", status), ("cursor", cursor)) if v}
        return await self._call("GET", "/v1/workflows", params)

    async def get_workflow(self, workflow_id: str) -> Reply:
        return await self._call("GET", f"/v1/workflows/{workflow_id}")

    async def cancel(self, workflow_id: str) -> Reply:
        return await self._call("POST", f"/v1/workflows/{workflow_id}/cancel")

    async def dead_events(self) -> Reply:
        return await self._call("GET", "/v1/dead-events")

    async def replay(self, event_id: str) -> Reply:
        return await self._call("POST", f"/v1/dead-events/{quote(event_id, safe='')}/replay")
