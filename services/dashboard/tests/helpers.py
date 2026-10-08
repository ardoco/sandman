"""Fixtures: dashboard app with fake upstreams (httpx.MockTransport), fake KG client and fake graph service."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
from sandman_agent import Settings
from sandman_dashboard.app import create_app
from sandman_dashboard.graph import GraphError, GraphService
from sandman_query.auth import RateLimiter

ADMIN_PASSWORD = "admin-password-LEAK-1"
SERVICE_TOKEN = "service-token-LEAK-2"
MEMGRAPH_PASSWORD = "memgraph-password-LEAK-3"
GATEWAY_PASSWORD = "gateway-password-LEAK-4"
SESSION_SECRET = "session-secret-LEAK-5"
SECRETS = [ADMIN_PASSWORD, SERVICE_TOKEN, MEMGRAPH_PASSWORD, GATEWAY_PASSWORD, SESSION_SECRET]
CSRF = re.compile(r'name="csrf" value="([^"]+)"')
FIXTURE = (Path(__file__).parent / "bifrost-metrics.txt").read_text()


class FakeKg:
    def __init__(self) -> None:
        self.up = True

    async def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        if not self.up:
            raise OSError("memgraph down")
        return [{"ok": 1, "c": 30 if "count(r)" in cypher else 12}]

    async def run_autocommit(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        if not self.up:
            raise OSError("memgraph down")
        return [
            {"storage info": "memory_res", "value": "2.00MiB"},
            {"storage info": "memory_limit", "value": "8.00GiB"},
        ]


class FakeGraph:
    labels = ["Project", "Requirement"]
    rel_types = ["HAS_ARTIFACT"]

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def projects(self) -> list[dict[str, Any]]:
        return [{"id": "demo", "name": "<script>alert(1)</script>"}]

    async def start(self, project: str | None, label: str | None, labels: list[str] | None = None) -> dict[str, Any]:
        return {"elements": [], "truncated": False}

    async def search(self, project: str | None, query: str, labels: list[str] | None = None) -> dict[str, Any]:
        return {"elements": [], "truncated": False}

    async def expand(self, project: str | None, node_id: str, labels: Any = None, types: Any = None) -> dict[str, Any]:
        return {"elements": [], "truncated": False}

    async def path(self, source: str, target: str, hops: int = 6) -> dict[str, Any]:
        return {"elements": [], "truncated": False, "found": False}

    async def cypher(self, statement: str) -> dict[str, Any]:
        self.statements.append(statement)
        if "boom" in statement:
            raise GraphError("query rejected")
        return {"columns": ["n"], "rows": [["<script>"]], "truncated": False, "elements": []}


@dataclass
class Env:
    http: httpx.AsyncClient
    app: Any
    kg: FakeKg
    graph: FakeGraph
    upstream: "Upstream"

    async def login(self, password: str = ADMIN_PASSWORD, username: str = "admin") -> httpx.Response:
        page = await self.http.get("/login")
        token = CSRF.search(page.text)
        assert token
        return await self.http.post("/login", data={"csrf": token[1], "username": username, "password": password})

    async def csrf(self, path: str = "/") -> str:
        token = CSRF.search((await self.http.get(path)).text)
        assert token
        return token[1]


class Upstream:
    """Behavior of the monitored services, switchable per test."""

    def __init__(self) -> None:
        self.down: set[str] = set()
        self.status_code: dict[str, int] = {}
        self.details: dict[str, Any] = {}
        self.gateway_status = 200
        self.hook: Callable[[httpx.Request], Any] | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if self.hook and (response := await self.hook(request)):
            return cast(httpx.Response, response)
        if host in self.down:
            raise httpx.ConnectError("refused", request=request)
        if host == "gateway":
            if path == "/health":
                return httpx.Response(200 if self.gateway_status != 0 else 503, text="ok")
            if self.gateway_status != 200:
                return httpx.Response(self.gateway_status, text=f"upstream body {GATEWAY_PASSWORD}")
            if path == "/metrics":
                return httpx.Response(200, text=FIXTURE)
            return httpx.Response(
                200, json={"budgets": [{"virtual_key_id": "vk-query", "max_limit": 10, "current_usage": 8.5}]}
            )
        if host == "nats":
            if path == "/healthz":
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(200, json={"account_details": [{"stream_detail": [{
                "name": "SANDMAN", "state": {"messages": 7, "bytes": 700},
                "consumer_detail": [{"name": "orchestrator", "num_pending": 1, "num_ack_pending": 2,
                                    "num_redelivered": 3}],
            }]}]})  # fmt: skip
        if path == "/readyz":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/v1/status":
            code = self.status_code.get(host, 200)
            if code != 200:
                return httpx.Response(code, text=f"error body {SERVICE_TOKEN}")
            return httpx.Response(
                200,
                json={"service": host, "version": "1.0", "state": "idle", "activeTasks": 0,
                      "details": self.details.get(host, {"mode": "live"})},
            )  # fmt: skip
        return httpx.Response(404)


TARGETS = {"orchestrator": "http://orchestrator:8000", "tlr-agent": "http://tlr-agent:8080"}


def make_app(
    upstream: Upstream, kg: FakeKg, graph: Any, state_db: str = ":memory:", **kwargs: Any
) -> tuple[Any, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    settings = Settings(
        service_name="dashboard", service_token=SERVICE_TOKEN, kg_uri="bolt://x", kg_password=MEMGRAPH_PASSWORD
    )
    app = create_app(
        settings,
        admin_password=ADMIN_PASSWORD,
        state_db=state_db,
        targets=TARGETS,
        nats_monitor_url="http://nats:8222",
        gateway_url="http://gateway:8080",
        gateway_password=GATEWAY_PASSWORD,
        http=http,
        kg=cast(Any, kg),
        graph=cast(GraphService, graph),
        start_poller=False,
        limiter=RateLimiter(),
        **kwargs,
    )
    return app, http
