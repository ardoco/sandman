"""query-service: REST API (/v1, bearer tokens only), console (/console) and health in one ASGI app.

Run: uvicorn --factory sandman_query.app:build --host 0.0.0.0 --port 8000
"""

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Mapping
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
import yaml
from argon2 import PasswordHasher
from fastapi import Depends, FastAPI, Query, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sandman_agent import Settings, StatusTracker
from sandman_agent.telemetry import FASTAPI_TELEMETRY, setup_logging
from starlette.exceptions import HTTPException

from sandman_query import __version__, console
from sandman_query.auth import Principal
from sandman_query.forward import Owners, problem, relay
from sandman_query.query import TRACELINK_COLUMNS, inconsistencies_csv, to_csv
from sandman_query.reader import Reader
from sandman_query.service import (
    COOKIE,
    QUIET,
    USERNAME,
    CypherBody,
    Login,
    Member,
    NewToken,
    NewUser,
    NlBody,
    ProjectId,
    Service,
    UserPatch,
    WorkflowBody,
    iso,
)
from sandman_query.store import Exists, UserStore

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"  # repository checkout


class Middleware:
    """Counts in-flight requests and adds the console's CSP header."""

    def __init__(self, app: Any, tracker: StatusTracker) -> None:
        self.app, self.tracker = app, tracker

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]

        async def send_csp(message: Any) -> None:
            if message["type"] == "http.response.start" and path.startswith(("/console", "/static")):
                message.setdefault("headers", []).append((b"content-security-policy", b"default-src 'self'"))
            await send(message)

        if path in QUIET:
            return await self.app(scope, receive, send_csp)
        async with self.tracker.track():
            await self.app(scope, receive, send_csp)


def create_app(
    settings: Settings,
    *,
    user_db: str | Path = ":memory:",
    admin_password: str | None = None,
    cookie_secure: bool = False,
    owner_urls: Mapping[str, str] | None = None,
    owner_transport: httpx.AsyncBaseTransport | None = None,
    reader: Reader | None = None,
    hasher: PasswordHasher | None = None,
    config_dir: Path | None = None,
    memory_limit: str = "100 MB",
) -> FastAPI:
    """ASGI app; owner_transport and reader let tests run without network."""
    settings.require("service_token")
    store = UserStore(user_db, hasher)
    if admin_password and store.user_count() == 0:
        store.create_user("admin", admin_password, ["admin"], must_change=True)
    if reader is None:
        settings.require("kg_uri")
        reader = Reader(settings.kg_uri or "", settings.kg_user, settings.kg_password)
    owners = Owners(owner_urls or {}, settings.service_token or "", owner_transport)
    svc = Service(
        settings,
        store,
        reader,
        owners,
        cookie_secure,
        config_dir or settings.config_dir or DEFAULT_CONFIG_DIR,
        memory_limit,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(settings.service_name, settings.log_file)
        yield
        await owners.close()
        await svc.reader.close()
        store.close()

    app = FastAPI(
        title="sandman-query-service",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        telemetry=FASTAPI_TELEMETRY,
    )  # the contract is contracts/openapi/query-service.yaml
    app.state.service = svc
    app.include_router(console.build_router(svc))
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, e: HTTPException) -> Response:
        if request.url.path.startswith("/console"):
            return console.error_response(e)
        resp = problem(e.status_code, HTTPStatus(e.status_code).phrase, str(e.detail))
        resp.headers.update(e.headers or {})
        return resp

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, e: RequestValidationError) -> JSONResponse:
        return problem(
            400, "invalid request", errors=[f"{'/'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]
        )

    def access(op: str) -> Any:
        """Dependency: authenticate (bearer only) and authorize `op`, on the path's projectId if present."""

        async def dependency(request: Request) -> Principal:
            who = svc.authenticate(request.headers.get("authorization"))
            svc.check(who, op, request.path_params.get("projectId"))
            return who

        return Depends(dependency)

    # ------------------------------------------------------------------ health

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        checks = {"memgraph": "down", "userstore": "down"}
        try:
            await svc.reader.run("RETURN 1", {}, 5)
            checks["memgraph"] = "ok"
        except Exception:  # noqa: BLE001 - any failure means not ready
            pass
        try:
            store.ping()
            checks["userstore"] = "ok"
        except Exception:  # noqa: BLE001
            pass
        ready = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if ready else "down", "checks": checks}, 200 if ready else 503)

    @app.get("/v1/status")
    async def status(request: Request) -> dict[str, Any]:
        who = svc.authenticate(request.headers.get("authorization"))
        if who.kind == "plugin" or (who.kind == "user" and not who.unrestricted_admin):
            raise HTTPException(403, "not allowed")
        return svc.tracker.snapshot()

    # -------------------------------------------------------------------- auth

    @app.post("/v1/auth/login")
    async def login(body: Login, request: Request) -> Response:
        sid = await svc.login(body.username, body.password, request)
        resp = JSONResponse(svc.me_doc(body.username))
        svc.set_cookie(resp, sid)
        return resp

    @app.post("/v1/auth/logout", status_code=204)
    async def logout(request: Request) -> Response:
        if sid := request.cookies.get(COOKIE):
            store.revoke_session(sid)
        resp = Response(status_code=204)
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/v1/auth/me")
    async def me(who: Annotated[Principal, access("me")]) -> dict[str, Any]:
        return svc.me_doc(who.name)

    @app.get("/v1/auth/tokens")
    async def list_tokens(who: Annotated[Principal, access("listTokens")]) -> list[dict[str, Any]]:
        return [svc.token_doc(t) for t in store.list_tokens(who.name)]

    @app.post("/v1/auth/tokens", status_code=201)
    async def create_token(body: NewToken, who: Annotated[Principal, access("createToken")]) -> dict[str, Any]:
        if who.token_project or who.token_max_role:
            raise HTTPException(403, "restricted tokens cannot create tokens")
        expires = iso(body.expiresAt) if body.expiresAt else None
        token_id, secret = store.create_token(who.name, body.name, body.projectId, body.maxRole, expires)
        return {**svc.token_doc(store.get_token(who.name, token_id) or {}), "secret": secret}

    @app.delete("/v1/auth/tokens/{tokenId}", status_code=204)
    async def revoke_token(tokenId: str, who: Annotated[Principal, access("revokeToken")]) -> Response:
        if who.token_project or who.token_max_role:
            raise HTTPException(403, "restricted tokens cannot revoke tokens")
        if not store.delete_token(who.name, tokenId):
            raise HTTPException(404, "not found")
        return Response(status_code=204)

    # ------------------------------------------------------------------- admin

    @app.get("/v1/admin/users")
    async def list_users(who: Annotated[Principal, access("listUsers")]) -> list[dict[str, Any]]:
        return [svc.user_doc(u["username"]) for u in store.list_users()]

    @app.post("/v1/admin/users", status_code=201)
    async def create_user(body: NewUser, who: Annotated[Principal, access("createUser")]) -> dict[str, Any]:
        try:
            await asyncio.to_thread(store.create_user, body.username, body.password, body.globalRoles)
        except Exists:
            raise HTTPException(409, "user exists") from None
        return svc.user_doc(body.username)

    @app.patch("/v1/admin/users/{username}")
    async def update_user(
        username: Annotated[str, PathParam(pattern=USERNAME)],
        body: UserPatch,
        who: Annotated[Principal, access("updateUser")],
    ) -> dict[str, Any]:
        # a reset by another admin forces a change at the next login
        must_change = True if body.password and username != who.name else None
        found = await asyncio.to_thread(
            store.update_user, username, body.disabled, body.password, body.globalRoles, must_change
        )
        if not found:
            raise HTTPException(404, "not found")
        return svc.user_doc(username)

    @app.put("/v1/admin/projects/{projectId}")
    async def put_project(
        projectId: ProjectId, request: Request, who: Annotated[Principal, access("putProject")]
    ) -> Response:
        resp = await svc.forward(
            "extraction-agent", "PUT", f"/v1/projects/{projectId}", who, projectId,
            body=await request.body(), headers={"content-type": "application/json"},
        )  # fmt: skip
        return relay(resp)

    @app.put("/v1/admin/projects/{projectId}/members/{username}", status_code=204)
    async def put_member(
        projectId: ProjectId,
        username: Annotated[str, PathParam(pattern=USERNAME)],
        body: Member,
        who: Annotated[Principal, access("putMember")],
    ) -> Response:
        if not store.set_member(projectId, username, body.role):
            raise HTTPException(404, "not found")
        return Response(status_code=204)

    @app.delete("/v1/admin/projects/{projectId}/members/{username}", status_code=204)
    async def delete_member(
        projectId: ProjectId,
        username: Annotated[str, PathParam(pattern=USERNAME)],
        who: Annotated[Principal, access("deleteMember")],
    ) -> Response:
        store.delete_member(projectId, username)
        return Response(status_code=204)

    # ---------------------------------------------------------------- projects

    @app.get("/v1/projects")
    async def list_projects(who: Annotated[Principal, access("listProjects")]) -> list[dict[str, Any]]:
        return await svc.projects(who)

    @app.post("/v1/projects/{projectId}/ingestions/check")
    async def check_ingestion(
        projectId: ProjectId, request: Request, who: Annotated[Principal, access("checkIngestion")]
    ) -> Response:
        resp = await svc.forward(
            "extraction-agent", "POST", f"/v1/projects/{projectId}/ingestions/check", who, projectId,
            body=await request.body(), headers={"content-type": "application/json"},
        )  # fmt: skip
        return relay(resp)

    @app.post("/v1/projects/{projectId}/ingestions", status_code=201)
    async def create_ingestion(
        projectId: ProjectId, request: Request, who: Annotated[Principal, access("createIngestion")]
    ) -> Response:
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith("multipart/form-data"):
            raise HTTPException(400, "multipart/form-data expected")
        headers = {"content-type": content_type}
        if "content-length" in request.headers:
            headers["content-length"] = request.headers["content-length"]
        resp = await svc.forward(  # the body is streamed to the owner, never buffered here
            "extraction-agent", "POST", f"/v1/projects/{projectId}/ingestions", who, projectId,
            body=request.stream(), headers=headers,
        )  # fmt: skip
        return relay(resp)

    @app.get("/v1/projects/{projectId}/inconsistencies")
    async def list_inconsistencies(
        projectId: ProjectId, request: Request, who: Annotated[Principal, access("listInconsistencies")]
    ) -> Response:
        return await svc.list_inconsistencies(who, projectId, request.query_params.multi_items())

    @app.get("/v1/inconsistencies/{inconsistencyId}")
    async def get_inconsistency(inconsistencyId: str, request: Request) -> Response:
        who = svc.authenticate(request.headers.get("authorization"))
        svc.check(who, "getInconsistency")
        return await svc.inconsistency_response(who, inconsistencyId, "getInconsistency")

    @app.post("/v1/inconsistencies/{inconsistencyId}/transitions")
    async def transition(inconsistencyId: str, request: Request) -> Response:
        who = svc.authenticate(request.headers.get("authorization"))
        svc.check(who, "transitionInconsistency")
        return await svc.transition(who, inconsistencyId, await request.body())

    @app.get("/v1/projects/{projectId}/workflows")
    async def list_workflows(
        projectId: ProjectId, request: Request, who: Annotated[Principal, access("listWorkflows")]
    ) -> Response:
        return await svc.list_workflows(who, projectId, request.query_params.multi_items())

    @app.post("/v1/projects/{projectId}/workflows", status_code=202)
    async def create_workflow(
        projectId: ProjectId, body: WorkflowBody, who: Annotated[Principal, access("createWorkflow")]
    ) -> Response:
        return await svc.create_workflow(who, projectId, body)

    @app.get("/v1/workflows/{workflowId}")
    async def get_workflow(workflowId: str, request: Request) -> Response:
        who = svc.authenticate(request.headers.get("authorization"))
        svc.check(who, "getWorkflow")
        resp = await svc.forward("orchestrator", "GET", f"/v1/workflows/{workflowId}", who, None)
        if resp.status_code == 404:
            raise HTTPException(404, "not found")
        if resp.status_code == 200:
            svc.check(who, "getWorkflow", resp.json()["projectId"])
        return relay(resp)

    # ------------------------------------------------------------------- query

    schema_doc = yaml.safe_load((settings.contracts_dir / "kg" / "schema.yaml").read_text(encoding="utf-8"))

    @app.get("/v1/schema")
    async def get_schema(who: Annotated[Principal, access("getSchema")]) -> dict[str, Any]:
        return schema_doc  # type: ignore[no-any-return]

    @app.post("/v1/query/cypher")
    async def query_cypher(body: CypherBody, request: Request) -> dict[str, Any]:
        who = svc.authenticate(request.headers.get("authorization"))
        svc.check(who, "queryCypher", body.projectId)
        if body.projectId is None and not who.unrestricted_admin:
            raise HTTPException(403, "projectId is required unless the caller is a global admin")
        return await svc.runner.run(who, body.projectId, body.cypher, body.params, body.limit, body.timeoutMs)

    @app.post("/v1/query/nl")
    async def query_nl(body: NlBody, request: Request) -> dict[str, Any]:
        who = svc.authenticate(request.headers.get("authorization"))
        svc.check(who, "queryNl", body.projectId)
        return await svc.nl.answer(who, body.projectId, body.question, body.strategy, body.mode, body.execute)

    @app.get("/v1/projects/{projectId}/export/tracelinks")
    async def export_tracelinks(
        projectId: ProjectId,
        who: Annotated[Principal, access("exportTraceLinks")],
        format: Literal["csv", "json"] = "csv",
        approach: str | None = None,
        minConfidence: Annotated[float, Query(ge=0, le=1)] = 0.0,
        includeRejected: bool = False,
    ) -> Response:
        links = await svc.runner.tracelinks(projectId, approach, minConfidence, includeRejected)
        if format == "json":
            return JSONResponse(links)
        keys = ["sourceKey", "targetKey", "linkType", "approach", "approachVersion", "confidence", "status", "runId"]
        return Response(to_csv(TRACELINK_COLUMNS, [[link[k] for k in keys] for link in links]), media_type="text/csv")

    @app.get("/v1/projects/{projectId}/export/inconsistencies")
    async def export_inconsistencies(
        projectId: ProjectId,
        who: Annotated[Principal, access("exportInconsistencies")],
        format: Literal["csv", "json"] = "json",
    ) -> Response:
        items = await svc.runner.inconsistencies(projectId)
        return JSONResponse(items) if format == "json" else Response(inconsistencies_csv(items), media_type="text/csv")

    app.add_middleware(Middleware, tracker=svc.tracker)  # type: ignore[arg-type]
    return app


def build(env: Mapping[str, str] | None = None) -> FastAPI:
    """App from SANDMAN_* settings and SANDMAN_USER_DB, SANDMAN_ADMIN_PASSWORD_FILE, SANDMAN_COOKIE_SECURE,
    SANDMAN_EXTRACTION_URL, SANDMAN_INCONSISTENCY_URL, SANDMAN_ORCHESTRATOR_URL."""
    env = os.environ if env is None else env
    settings = Settings.from_env(env)
    password_file = env.get("SANDMAN_ADMIN_PASSWORD_FILE")
    return create_app(
        settings,
        user_db=env.get("SANDMAN_USER_DB", "/data/users.db"),
        admin_password=Path(password_file).read_text(encoding="utf-8").strip() if password_file else None,
        cookie_secure=env.get("SANDMAN_COOKIE_SECURE", "").lower() == "true",
        owner_urls={
            "extraction-agent": env.get("SANDMAN_EXTRACTION_URL", ""),
            "inconsistency-agent": env.get("SANDMAN_INCONSISTENCY_URL", ""),
            "orchestrator": env.get("SANDMAN_ORCHESTRATOR_URL", ""),
        },
    )
