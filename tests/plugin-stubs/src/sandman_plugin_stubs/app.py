"""Plugin stubs (Stage 2 §4.6): extract via A2A (port 8000), detect via plain HTTP (port 8001).

External systems per ADR-0012: they return results only (never write the KG), read artifacts from the read-only
artifact store, and read the KG through the query-service with their own token (caller plugin:plugin-stubs).
"""

import hmac
import logging
import os
import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from sandman_agent import ContractError, Contracts, Settings, SkillContext, SkillError, SkillResult, serve
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route

from sandman_plugin_stubs import __version__

log = logging.getLogger(__name__)

PLUGIN_ID = "plugin-stubs"
CARD = Path(__file__).with_name("agent-card.json")
COUNT = "MATCH (e:Element {projectId: $projectId}) RETURN count(e)"
CLASS = re.compile(r"\bclass\s+([A-Za-z_]\w*)")
PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
IMPORT = re.compile(r"^\s*import\s+([\w.]+)\s*;", re.M)
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
REQ = re.compile(r"REQ-\d+")


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _classes(source: str) -> list[tuple[str, str, int]]:
    """(name, qualified name, line) per `class <Name>` match."""
    package = PACKAGE.search(source)
    prefix = package[1] + "." if package else ""
    return [(m[1], prefix + m[1], source.count("\n", 0, m.start()) + 1) for m in CLASS.finditer(source)]


class Extract:
    """sandman.plugin.extract: regex class extraction (CodeUnit/TestCase), DEPENDS_ON, REQ-n references."""

    id = "sandman.plugin.extract"

    async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
        assert ctx.settings.artifact_store is not None
        store = ctx.settings.artifact_store.resolve()

        def read(storage_path: str) -> str:
            path = Path(storage_path).resolve()
            if not path.is_relative_to(store):
                raise SkillError("INVALID_INPUT", f"{storage_path} is outside the artifact store", False)
            return path.read_text(encoding="utf-8", errors="replace")

        known: dict[str, dict[str, str]] = {}  # qualified name -> locator, across all current artifacts
        for a in [*input["context"]["currentArtifacts"], *input["artifacts"]]:
            for _, qn, _ in _classes(read(a["storagePath"])):
                known[qn] = {"path": a["path"], "localKey": qn}

        elements: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        references: list[dict[str, Any]] = []
        for a in input["artifacts"]:
            source, path = read(a["storagePath"]), a["path"]
            label = "TestCase" if a["kind"] == "test" else "CodeUnit"
            file = {"path": path, "localKey": path}
            elements.append({**file, "label": label, "kind": "file", "name": PurePosixPath(path).name})
            classes = []
            for name, qn, line in _classes(source):
                classes.append({"path": path, "localKey": qn})
                elements.append(
                    {
                        "path": path,
                        "localKey": qn,
                        "label": label,
                        "kind": "type",
                        "name": name,
                        "qualifiedName": qn,
                        "startLine": line,
                        "parentLocalKey": path,
                    }
                )
            for imported in IMPORT.findall(source):
                if imported in known:
                    relations += [{"type": "DEPENDS_ON", "source": c, "target": known[imported]} for c in classes]
            # ponytail: references attach to the file's first class, per-class scoping if a real plugin needs it
            owner = classes[0] if classes else file
            link_type = "verifies" if a["kind"] == "test" else "implements"
            for ref in dict.fromkeys(REQ.findall("\n".join(COMMENT.findall(source)))):
                references.append({"source": owner, "referenceText": ref, "linkType": link_type})
        return SkillResult({"elements": elements, "relations": relations, "explicitReferences": references})


def create_extract_app(settings: Settings, contracts: Contracts | None = None) -> Starlette:
    """A2A server in plugin mode (inbound token), plus /healthz."""
    settings.require("plugin_inbound_token", "artifact_store")
    app = serve(CARD, [Extract()], settings, contracts=contracts)
    app.router.routes.append(Route("/healthz", healthz))
    return app


def _problem(status: int, detail: str) -> JSONResponse:
    title = "Bad Request" if status == 400 else "Service Unavailable"
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail}
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def _origin(url: str) -> tuple[str, str, int | None]:
    u = urlsplit(url.strip())
    return u.scheme.lower(), (u.hostname or "").lower(), u.port or {"http": 80, "https": 443}.get(u.scheme.lower())


def create_detect_app(
    settings: Settings, contracts: Contracts | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    """Plain-HTTP detect plugin (contracts/openapi/plugin-http.yaml)."""
    settings.require("plugin_inbound_token", "service_token")
    inbound, outbound = str(settings.plugin_inbound_token), str(settings.service_token)
    contracts = contracts or Contracts(settings.contracts_dir)
    query_url = os.environ.get("SANDMAN_QUERY_SERVICE_URL", "http://query-service:8000").rstrip("/")
    query_service = httpx.AsyncClient(headers={"Authorization": f"Bearer {outbound}"}, transport=transport, timeout=30)
    app = FastAPI(title=PLUGIN_ID, version=__version__)

    def owner(authorization: str | None = Header(None)) -> None:
        token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else ""
        if not token or not hmac.compare_digest(token, inbound):
            raise HTTPException(401, "missing or invalid token")

    app.add_api_route("/healthz", healthz, methods=["GET"])

    @app.get("/manifest", dependencies=[Depends(owner)])
    def manifest() -> dict[str, Any]:
        return {"id": PLUGIN_ID, "version": __version__, "operations": ["detect"], "types": [], "usesLlm": False}

    @app.post("/v1/detect", dependencies=[Depends(owner)], response_model=None)
    async def detect(body: Annotated[Any, Body()]) -> dict[str, Any] | JSONResponse:
        try:
            contracts.validate("skills.schema.json#/$defs/PluginDetectInput", body)
        except ContractError as e:
            return _problem(400, str(e))
        hint = body.get("queryServiceUrl")
        if hint is not None and _origin(hint) != _origin(query_url):
            return _problem(400, "queryServiceUrl does not match the configured query-service")
        project = body["projectId"]
        try:
            resp = await query_service.post(
                query_url + "/v1/query/cypher", json={"projectId": project, "cypher": COUNT}
            )
            resp.raise_for_status()
            count = resp.json()["rows"][0][0]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as e:
            return _problem(503, f"query-service: {e}")
        log.info("project %s has %s elements", project, count)
        return {"findings": [], "warnings": [f"plugin-stubs: project {project} has {count} elements"]}

    return app
