"""dashboard: system overview for admins (ADR-0016). Server-rendered pages plus session-authenticated JSON endpoints.

Run: python -m sandman_dashboard
"""

import asyncio
import contextlib
import hmac
import ipaddress
import json
import os
import secrets
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qsl

import httpx
import jinja2
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from neo4j import AsyncGraphDatabase
from sandman_agent import Settings
from sandman_agent.kg import KgClient
from sandman_agent.telemetry import FASTAPI_TELEMETRY, setup_logging
from sandman_query.auth import RateLimiter
from starlette.exceptions import HTTPException

from sandman_dashboard import __version__, insights, ops
from sandman_dashboard.events_view import EventViewer
from sandman_dashboard.gateway import GatewayClient
from sandman_dashboard.graph import GraphError, GraphService, schema_names
from sandman_dashboard.orchestrator import OrchestratorClient
from sandman_dashboard.poller import Poller, parse_targets
from sandman_dashboard.progress import progress
from sandman_dashboard.projects import ProjectStats
from sandman_dashboard.resources import Cadvisor
from sandman_dashboard.sampler import Sampler
from sandman_dashboard.store import SessionStore

COOKIE = "sandman_dashboard_session"
LOGIN_CSRF = "sandman_dashboard_login_csrf"
ADMIN = "admin"
HERE = Path(__file__).parent
MAX_FORM = 16 * 1024
CSP = "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
TEMPLATES = jinja2.Environment(
    loader=jinja2.FileSystemLoader(HERE / "templates"), autoescape=jinja2.select_autoescape(["html"])
)


class ProxyHeaders:
    """Client address from X-Forwarded-For, only when the TCP peer is one of the trusted proxies (resolved by name)."""

    def __init__(self, app: Any, hosts: list[str], ttl: float = 30.0) -> None:
        self.app, self.hosts, self.ttl = app, hosts, ttl
        self._ips: set[str] = set()
        self._at = float("-inf")

    async def _trusted(self) -> set[str]:
        if time.monotonic() - self._at >= self.ttl:
            ips: set[str] = set()
            for host in self.hosts:
                with contextlib.suppress(OSError):
                    ips |= {str(i[4][0]) for i in await asyncio.get_running_loop().getaddrinfo(host, None)}
            self._ips, self._at = ips, time.monotonic()
        return self._ips

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if (
            scope["type"] == "http"
            and self.hosts
            and scope.get("client")
            and scope["client"][0] in await self._trusted()
        ):
            forwarded = dict(scope["headers"]).get(b"x-forwarded-for")
            if forwarded:
                candidate = forwarded.decode("latin-1").split(",")[-1].strip()
                with contextlib.suppress(ValueError):
                    scope = {**scope, "client": (str(ipaddress.ip_address(candidate)), 0)}
        await self.app(scope, receive, send)


async def read_form(request: Request) -> dict[str, str]:
    """urlencoded form body of at most MAX_FORM bytes (413 beyond)."""
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        raise HTTPException(400, "invalid Content-Length") from None
    if declared > MAX_FORM:
        raise HTTPException(413, "request body too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_FORM:
            raise HTTPException(413, "request body too large")
    return dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))


@dataclass
class Ctx:
    username: str
    csrf: str
    sid: str


def _number(value: Any, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}"


def _ago(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        seconds = (datetime.now(UTC) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return "n/a"
    if seconds < 90:
        return f"{max(int(seconds), 0)} s ago"
    return f"{int(seconds // 60)} min ago" if seconds < 5400 else f"{int(seconds // 3600)} h ago"


def _pretty(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


MAX_JSON = 64 * 1024


def _pjson(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    return text if len(text) <= MAX_JSON else text[:MAX_JSON] + "\n... truncated"


def _step_duration(step: Mapping[str, Any]) -> str:
    try:
        seconds = (datetime.fromisoformat(step["endedAt"]) - datetime.fromisoformat(step["startedAt"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return ""
    return f"{seconds * 1000:,.0f} ms" if seconds < 1 else f"{seconds:,.1f} s"


TEMPLATES.filters.update(num=_number, ago=_ago, pretty=_pretty, pjson=_pjson, step_duration=_step_duration)
TEMPLATES.filters["progress"] = progress
TEMPLATES.filters["seconds"] = lambda v: "n/a" if v is None else f"{v * 1000:,.0f} ms" if v < 1 else f"{v:,.2f} s"
TEMPLATES.filters["mib"] = lambda v: "n/a" if v is None else f"{v / 2**20:,.0f} MiB"
TEMPLATES.filters["cost"] = lambda v: "n/a" if v is None else f"${v:,.4f}"
TEMPLATES.filters["percent"] = lambda v: f"{v * 100:,.0f} %"


def create_app(
    settings: Settings,
    *,
    admin_password: str,
    state_db: str | Path = ":memory:",
    cookie_secure: bool = False,
    targets: Mapping[str, str] | None = None,
    nats_monitor_url: str | None = None,
    gateway_url: str | None = None,
    gateway_password: str | None = None,
    cadvisor_url: str | None = None,
    poll_interval: float = 5.0,
    sample_interval: float = 30.0,
    history_days: float = 14.0,
    http: httpx.AsyncClient | None = None,
    kg: KgClient | None = None,
    graph: GraphService | None = None,
    events: EventViewer | None = None,
    start_poller: bool = True,
    limiter: RateLimiter | None = None,
    trusted_proxies: list[str] | None = None,
) -> FastAPI:
    """ASGI app; http, kg and graph let tests run without network."""
    settings.require("service_token")
    store = SessionStore(state_db)
    limiter = limiter or RateLimiter()
    driver = None
    if graph is None or kg is None:
        settings.require("kg_uri")
        auth = (settings.kg_user, settings.kg_password or "") if settings.kg_user else None
        driver = AsyncGraphDatabase.driver(settings.kg_uri or "", auth=auth)
        kg = kg or KgClient(settings.kg_uri or "", settings.kg_user, settings.kg_password, driver=driver)
        if graph is None:
            labels, rel_types = schema_names(settings.contracts_dir)
            graph = GraphService(driver, labels, rel_types)
    client = http or httpx.AsyncClient()
    gateway = GatewayClient(gateway_url, gateway_password or "", client) if gateway_url else None
    viewer = events or (EventViewer(settings.nats_url) if settings.nats_url and start_poller else None)
    orchestrator = (
        OrchestratorClient(targets["orchestrator"], settings.service_token or "", client)
        if "orchestrator" in (targets or {})
        else None
    )
    poller = Poller(
        targets or {},
        client,
        settings.service_token or "",
        interval=poll_interval,
        kg=kg,
        nats_monitor_url=nats_monitor_url,
        gateway=gateway,
        orchestrator=orchestrator,
    )
    sampler = Sampler(store.db, poller, interval=sample_interval, retention_days=history_days)
    secret_values = [admin_password, settings.service_token, settings.kg_password, gateway_password]

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(settings.service_name, settings.log_file)
        if start_poller:
            poller.start()
            sampler.start()
            if viewer is not None:
                viewer.start()
        yield
        await poller.stop()
        await sampler.stop()
        if viewer is not None:
            await viewer.stop()
        if http is None:
            await client.aclose()
        if driver is not None:
            await driver.close()
        store.close()

    app = FastAPI(
        title="sandman-dashboard",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        telemetry=FASTAPI_TELEMETRY,
    )
    app.state.poller, app.state.store, app.state.secrets = poller, store, secret_values
    app.state.sampler = sampler
    assert graph is not None and kg is not None
    graph_service, kg_client = graph, kg

    @app.middleware("http")
    async def headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    def redact(text: str) -> str:
        for value in secret_values:
            if value:
                text = text.replace(value, "[redacted]")
        return text

    def page(request: Request, ctx: Ctx | None, template: str, status: int = 200, **data: Any) -> HTMLResponse:
        base = {
            "user": ctx.username if ctx else None,
            "csrf": ctx.csrf if ctx else "",
            "path": request.url.path,
            "status": status,
            "alerts": list(poller.alerts.active.values()) if ctx else [],
            "redact": redact,
        }
        return HTMLResponse(TEMPLATES.get_template(template).render(**{**base, **data}), status)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, e: HTTPException) -> Response:
        path = request.url.path
        if e.status_code == 303:
            return RedirectResponse((e.headers or {})["Location"], 303)
        if path.startswith(("/api/", "/fragments/")):
            headers = dict(e.headers or {})
            if e.status_code == 401:
                headers["HX-Redirect"] = "/login"
            return JSONResponse({"error": str(e.detail)}, e.status_code, headers=headers)
        return page(request, None, "error.html", e.status_code, message=str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, e: RequestValidationError) -> Response:
        return JSONResponse({"error": "invalid request"}, 422)

    @app.exception_handler(GraphError)
    async def graph_error(request: Request, e: GraphError) -> Response:
        return JSONResponse({"error": str(e)}, e.status)

    def session(request: Request) -> Ctx:
        sid = request.cookies.get(COOKIE)
        row = store.get(sid, touch=not request.url.path.startswith("/fragments/")) if sid else None
        if row is None:
            if request.url.path.startswith(("/api/", "/fragments/")):
                raise HTTPException(401, "authentication required")
            raise HTTPException(303, headers={"Location": "/login"})
        return Ctx(row["username"], row["csrf"], sid or "")

    Session = Annotated[Ctx, Depends(session)]

    def verify_csrf(sent: Any, expected: str) -> None:
        if not expected or not isinstance(sent, str) or not hmac.compare_digest(sent.encode(), expected.encode()):
            raise HTTPException(403, "missing or invalid CSRF token")

    def set_cookie(response: Response, sid: str) -> None:
        response.set_cookie(COOKIE, sid, httponly=True, samesite="strict", secure=cookie_secure, path="/")

    # ------------------------------------------------------------------ health

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        checks = {"memgraph": "down", "sessionstore": "down"}
        try:
            async with asyncio.timeout(5):
                await kg_client.read("RETURN 1 AS ok")
            checks["memgraph"] = "ok"
        except Exception:  # noqa: BLE001 - any failure means not ready
            pass
        try:
            store.ping()
            checks["sessionstore"] = "ok"
        except Exception:  # noqa: BLE001
            pass
        ready = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if ready else "down", "checks": checks}, 200 if ready else 503)

    # ------------------------------------------------------------ login/logout

    @app.get("/login")
    async def login_page(request: Request) -> HTMLResponse:
        token = secrets.token_urlsafe(24)
        response = page(request, None, "login.html", csrf=token)
        response.set_cookie(LOGIN_CSRF, token, httponly=True, samesite="strict", secure=cookie_secure, path="/login")
        return response

    @app.post("/login")
    async def login(request: Request) -> Response:
        data = await read_form(request)
        token = request.cookies.get(LOGIN_CSRF, "")
        verify_csrf(data.get("csrf"), token)
        username, password = str(data.get("username", "")), str(data.get("password", ""))
        keys = (f"u:{username.lower()}", f"ip:{request.client.host if request.client else '?'}")

        def failed(status: int, message: str) -> HTMLResponse:
            response = page(request, None, "login.html", status, csrf=token, message=message, error=True)
            if status == 429:
                response.headers["Retry-After"] = "900"
            return response

        if limiter.blocked(*keys):
            return failed(429, "Too many failed logins. Try again later.")
        same_user = hmac.compare_digest(username.encode(), ADMIN.encode())
        same_password = hmac.compare_digest(password.encode(), admin_password.encode())
        if not (same_user and same_password):
            limiter.fail(*keys)
            return failed(401, "Invalid credentials.")
        limiter.reset(keys[0])
        sid, _ = store.create(ADMIN)
        response = RedirectResponse("/", 303)
        set_cookie(response, sid)
        response.delete_cookie(LOGIN_CSRF, path="/login")
        return response

    @app.post("/logout")
    async def logout(request: Request, ctx: Session) -> Response:
        verify_csrf((await read_form(request)).get("csrf"), ctx.csrf)
        store.revoke(ctx.sid)
        response = RedirectResponse("/login", 303)
        response.delete_cookie(COOKIE, path="/")
        return response

    # ------------------------------------------------------------------- pages

    def service_rows() -> list[Any]:
        entries = poller.entries
        names = [*poller.targets, "memgraph", "nats", "llm-gateway"]
        return [entries[n] for n in names if n in entries]

    @app.get("/")
    async def services_page(request: Request, ctx: Session) -> HTMLResponse:
        return page(request, ctx, "services.html", rows=service_rows(), waiting=not poller.entries)

    @app.get("/fragments/services")
    async def services_fragment(request: Request, ctx: Session) -> HTMLResponse:
        return page(request, ctx, "_services.html", rows=service_rows(), waiting=not poller.entries)

    @app.get("/fragments/alerts")
    async def alerts_fragment(request: Request, ctx: Session) -> HTMLResponse:
        return page(request, ctx, "_alerts.html")

    @app.get("/gateway")
    async def gateway_page(request: Request, ctx: Session) -> HTMLResponse:
        return page(request, ctx, "gateway.html", stats=poller.gateway_snapshot, configured=gateway is not None)

    @app.get("/fragments/gateway")
    async def gateway_fragment(request: Request, ctx: Session) -> HTMLResponse:
        return page(request, ctx, "_gateway.html", stats=poller.gateway_snapshot, configured=gateway is not None)

    @app.get("/graph")
    async def graph_page(request: Request, ctx: Session) -> HTMLResponse:
        try:
            projects = await graph_service.projects()
            message = None
        except GraphError as e:
            projects, message = [], str(e)
        return page(
            request, ctx, "graph.html", projects=projects, labels=graph_service.labels,
            rel_types=graph_service.rel_types, message=message, error=message is not None,
        )  # fmt: skip

    ops.register(
        app,
        orchestrator=orchestrator,
        poller=poller,
        events=viewer,
        session=Session,
        page=page,
        verify_csrf=verify_csrf,
        read_form=read_form,
    )

    insights.register(
        app,
        graph=graph_service,
        stats=ProjectStats(graph_service),
        db=store.db,
        cadvisor=Cadvisor(cadvisor_url, client),
        poller=poller,
        session=Session,
        page=page,
    )

    # --------------------------------------------------------------------- API

    @app.get("/api/services")
    async def api_services(ctx: Session) -> dict[str, Any]:
        return {"services": [asdict(e) for e in service_rows()]}

    @app.get("/api/gateway")
    async def api_gateway(ctx: Session) -> dict[str, Any]:
        snap = poller.gateway_snapshot
        if snap is None:
            return {"available": False, "rows": [], "budgets": None}
        return {
            "available": True,
            "rows": [asdict(r) for r in snap.rows],
            "budgets": None if snap.budgets is None else [
                {"key": b.key_name, "limit": b.limit, "usage": b.usage, "warn": b.warn} for b in snap.budgets
            ],
        }  # fmt: skip

    @app.get("/api/graph/projects")
    async def api_projects(ctx: Session) -> dict[str, Any]:
        return {"projects": await graph_service.projects()}

    @app.get("/api/graph/start")
    async def api_start(
        ctx: Session,
        project: str | None = None,
        label: str | None = None,
        labels: Annotated[list[str] | None, Query()] = None,
    ) -> dict[str, Any]:
        return await graph_service.start(project, label, labels)

    @app.get("/api/graph/search")
    async def api_search(
        ctx: Session, q: str, project: str | None = None, labels: Annotated[list[str] | None, Query()] = None
    ) -> dict[str, Any]:
        return await graph_service.search(project, q, labels)

    @app.get("/api/graph/expand")
    async def api_expand(
        ctx: Session,
        id: str,
        project: str | None = None,
        labels: Annotated[list[str] | None, Query()] = None,
        types: Annotated[list[str] | None, Query()] = None,
    ) -> dict[str, Any]:
        return await graph_service.expand(project, id, labels, types)

    @app.get("/api/graph/path")
    async def api_path(ctx: Session, source: str, target: str, hops: int = 6) -> dict[str, Any]:
        return await graph_service.path(source, target, hops)

    @app.post("/api/cypher")
    async def api_cypher(request: Request, ctx: Session) -> dict[str, Any]:
        verify_csrf(request.headers.get("x-csrf-token"), ctx.csrf)
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, "invalid JSON") from None
        statement = body.get("cypher") if isinstance(body, dict) else None
        if not isinstance(statement, str) or not statement.strip() or len(statement) > 20000:
            raise HTTPException(422, "cypher statement required (at most 20000 characters)")
        return await graph_service.cypher(statement)

    if trusted_proxies:
        app.add_middleware(ProxyHeaders, hosts=trusted_proxies)
    return app


def build(env: Mapping[str, str] | None = None) -> FastAPI:
    """App from SANDMAN_* settings, SANDMAN_DASHBOARD_ADMIN_PASSWORD_FILE, SANDMAN_STATE_DB, SANDMAN_COOKIE_SECURE,
    SANDMAN_POLL_INTERVAL, SANDMAN_SAMPLE_INTERVAL, SANDMAN_HISTORY_DAYS, SANDMAN_CADVISOR_URL, SANDMAN_TARGETS,
    SANDMAN_NATS_MONITOR_URL, SANDMAN_GATEWAY_URL and SANDMAN_GATEWAY_ADMIN_PASSWORD_FILE,
    SANDMAN_TRUSTED_PROXIES (host names or IPs allowed to set X-Forwarded-For, default edge-proxy)."""
    env = os.environ if env is None else env
    settings = Settings.from_env(env)

    def secret(name: str) -> str | None:
        path = env.get(f"{name}_FILE")
        return Path(path).read_text(encoding="utf-8").strip() if path else env.get(name) or None

    admin_password = secret("SANDMAN_DASHBOARD_ADMIN_PASSWORD")
    if not admin_password:
        raise ValueError("dashboard: missing configuration SANDMAN_DASHBOARD_ADMIN_PASSWORD_FILE")
    return create_app(
        settings,
        admin_password=admin_password,
        state_db=env.get("SANDMAN_STATE_DB", "/data/dashboard.db"),
        cookie_secure=env.get("SANDMAN_COOKIE_SECURE", "").lower() == "true",
        targets=parse_targets(env.get("SANDMAN_TARGETS", "")),
        nats_monitor_url=env.get("SANDMAN_NATS_MONITOR_URL") or None,
        gateway_url=env.get("SANDMAN_GATEWAY_URL") or None,
        gateway_password=secret("SANDMAN_GATEWAY_ADMIN_PASSWORD"),
        cadvisor_url=env.get("SANDMAN_CADVISOR_URL") or None,
        poll_interval=float(env.get("SANDMAN_POLL_INTERVAL", "5")),
        sample_interval=float(env.get("SANDMAN_SAMPLE_INTERVAL", "30")),
        history_days=float(env.get("SANDMAN_HISTORY_DAYS", "14")),
        trusted_proxies=[h.strip() for h in env.get("SANDMAN_TRUSTED_PROXIES", "edge-proxy").split(",") if h.strip()],
    )
