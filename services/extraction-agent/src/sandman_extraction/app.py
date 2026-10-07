"""extraction-agent: REST intake API (/v1) and A2A skill (/a2a) in one ASGI app.

Run: uvicorn --factory sandman_extraction.app:create_app --host 0.0.0.0 --port 8000
"""

import logging
import os
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
import nats
import yaml
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sandman_agent import (
    ArtifactStore,
    Contracts,
    Event,
    EventPublisher,
    KgClient,
    PublishError,
    Settings,
    StatusTracker,
    TokenVerifier,
    ensure_stream,
    require_service,
    serve,
    trusted_actor,
)
from sandman_agent.ids import sha256_hex
from sandman_agent.kg import now
from sandman_agent.telemetry import setup_logging
from starlette.exceptions import HTTPException

from sandman_extraction import ingest
from sandman_extraction.extract import ExtractSkill

log = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"  # repository checkout
PROJECT_ID = r"^[a-z0-9][a-z0-9-]{1,62}$"
ACTOR = re.compile(r"^(user|service):[A-Za-z0-9._@-]+$")
ProjectId = Annotated[str, PathParam(pattern=PROJECT_ID)]


class ProjectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    llmPolicy: Literal["cloud-allowed", "local-only"]


class Service:
    """Shared state of REST handlers and the skill; also the EventSink handed to serve()."""

    def __init__(self, settings: Settings, max_archive_bytes: int | None = None) -> None:
        settings.require("kg_uri", "nats_url", "artifact_store")
        self.settings = settings
        self.contracts = Contracts(settings.contracts_dir)
        self.store = ArtifactStore(settings.artifact_store)  # type: ignore[arg-type]
        raw = ((settings.config_dir or DEFAULT_CONFIG_DIR) / "extraction" / "plugins.yaml").read_bytes()
        self.config: dict[str, Any] = yaml.safe_load(raw) or {}
        self.config_hash = sha256_hex(raw)
        self.status = StatusTracker(settings.service_name)
        self.max_archive_bytes = max_archive_bytes or int(os.environ.get("SANDMAN_MAX_ARCHIVE_BYTES", 1 << 30))
        self.kg = KgClient(settings.kg_uri, settings.kg_user, settings.kg_password)  # type: ignore[arg-type]
        self.events: Any = None
        self._nc: Any = None

    async def start(self) -> None:
        """Schema init, stream, re-publish of pending ingestion events."""
        await self.kg.init_schema(self.settings.contracts_dir / "kg" / "init.cypher")
        self._nc = await nats.connect(self.settings.nats_url or "")
        js = self._nc.jetstream()
        await ensure_stream(js)
        self.events = EventPublisher(js, self.contracts, self.settings.service_name)
        try:
            await ingest.republish_pending(self.kg, self)
        except PublishError:
            log.exception("re-publishing pending ingestion events failed; retried before the next intake")

    async def stop(self) -> None:
        if self._nc is not None:
            await self._nc.close()
        await self.kg.close()

    async def publish(self, type: str, subject: str, data: Mapping[str, Any], **kwargs: Any) -> Event:
        """EventSink: delegate to the publisher created at startup."""
        event: Event = await self.events.publish(type, subject, data, **kwargs)
        return event


def _problem(status: int, detail: str, errors: list[str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"type": "about:blank", "title": HTTPStatus(status).phrase, "status": status}
    body["detail"] = detail
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def create_app(settings: Settings | None = None, plugin_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """ASGI app; plugin_transport lets tests route plugin calls in-process."""
    settings = settings or Settings.from_env()
    svc = Service(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(settings.service_name, settings.log_file)
        await svc.start()
        try:
            yield
        finally:
            await svc.stop()

    app = FastAPI(title="sandman-extraction-agent", lifespan=lifespan)
    app.state.service = svc
    Caller = Annotated[str, Depends(require_service(TokenVerifier(settings.service_verify)))]

    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, exc: HTTPException) -> JSONResponse:
        return _problem(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(400, "invalid request", [f"{'/'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()])

    @app.exception_handler(PublishError)
    async def unpublished(request: Request, exc: PublishError) -> JSONResponse:
        return _problem(503, f"ingestion stored, event not published (re-published later): {exc}")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        checks = {"memgraph": "down", "nats": "down"}
        try:
            await svc.kg.read("RETURN 1 AS ok")
            checks["memgraph"] = "ok"
        except Exception:  # noqa: BLE001 - any failure means not ready
            pass
        if svc._nc is not None and svc._nc.is_connected:
            checks["nats"] = "ok"
        ready = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if ready else "down", "checks": checks}, status_code=200 if ready else 503)

    async def project_or_404(project_id: str) -> dict[str, Any]:
        found = await svc.kg.read("MATCH (p:Project {id: $id}) RETURN properties(p) AS p", id=project_id)
        if not found:
            raise HTTPException(404, f"project {project_id} not found")
        p = found[0]["p"]
        return {k: p[k] for k in ("id", "name", "llmPolicy", "createdAt")}

    @app.put("/v1/projects/{projectId}")
    async def put_project(projectId: ProjectId, spec: ProjectSpec, caller: Caller, request: Request) -> dict[str, Any]:
        trusted_actor(caller, request.headers)
        await svc.kg.write(
            "MERGE (p:Project {id: $id}) ON CREATE SET p.createdAt = $now, p.ingestionSeq = 0 "
            "SET p.name = $name, p.llmPolicy = $policy",
            id=projectId, now=now(), name=spec.name, policy=spec.llmPolicy,
        )  # fmt: skip
        return await project_or_404(projectId)

    @app.get("/v1/projects/{projectId}")
    async def get_project(projectId: ProjectId, caller: Caller) -> dict[str, Any]:
        return await project_or_404(projectId)

    @app.post("/v1/projects/{projectId}/ingestions/check")
    async def check(projectId: ProjectId, manifest: ingest.Manifest, caller: Caller) -> dict[str, list[str]]:
        await project_or_404(projectId)
        return {"missing": ingest.missing_hashes(svc.store, projectId, manifest)}

    @app.post("/v1/projects/{projectId}/ingestions", status_code=201)
    async def create_ingestion(
        projectId: ProjectId,
        caller: Caller,
        request: Request,
        manifest: Annotated[str, Form()],
        archive: Annotated[UploadFile | None, File()] = None,
    ) -> dict[str, Any]:
        actor = trusted_actor(caller, request.headers) or f"service:{caller}"
        if not ACTOR.match(actor):
            raise HTTPException(400, "invalid X-Sandman-Actor")
        try:
            parsed = ingest.Manifest.model_validate_json(manifest)
        except ValidationError as e:
            raise RequestValidationError(e.errors()) from None
        async with svc.status.track():
            await ingest.republish_pending(svc.kg, svc)
            await project_or_404(projectId)
            files: dict[str, bytes] = {}
            if archive is not None:
                data = await archive.read(svc.max_archive_bytes + 1)
                if len(data) > svc.max_archive_bytes:
                    raise HTTPException(400, f"archive exceeds {svc.max_archive_bytes} bytes")
                files = ingest.read_archive(data, svc.max_archive_bytes)
            ing_id = await ingest.ingest(svc.kg, svc.store, projectId, parsed, files, actor)
            ing = await ingest.load_ingestion(svc.kg, projectId, ing_id)
            assert ing is not None
            if not ing["eventPublished"]:
                await ingest.publish(svc.kg, svc, ing)
            return ingest.response(ing)

    @app.get("/v1/projects/{projectId}/ingestions/{ingestionId}")
    async def get_ingestion(
        projectId: ProjectId, ingestionId: Annotated[str, PathParam(pattern=r"^[a-z]+_[0-9a-f]{32}$")], caller: Caller
    ) -> dict[str, Any]:
        ing = await ingest.load_ingestion(svc.kg, projectId, ingestionId)
        if ing is None:
            raise HTTPException(404, f"ingestion {ingestionId} not found")
        return ingest.response(ing)

    card = settings.contracts_dir / "agent-cards" / "extraction-agent.json"
    skill = ExtractSkill(svc, plugin_transport)
    app.mount("/", serve(card, [skill], settings, events=svc, contracts=svc.contracts, status=svc.status))
    return app
