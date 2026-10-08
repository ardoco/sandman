"""document-converter: POST /v1/convert turns a PDF/DOCX of the (read-only mounted) artifact store into Markdown.

Run: uvicorn --factory sandman_document_converter.app:create_app --host 0.0.0.0 --port 8000
Only the extraction-agent may convert; every service token may read /v1/status. Conversions beyond the concurrency
bound wait in a queue, so callers need a timeout above timeout + queue time.
"""

import asyncio
import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field
from sandman_agent import Settings, StatusTracker, TokenVerifier, require_service, status_route
from starlette.exceptions import HTTPException

from sandman_document_converter.convert import ConversionError, Limits, to_markdown

CALLER = "extraction-agent"
MAGIC = {"pdf": b"%PDF-", "docx": b"PK\x03\x04"}


class ConvertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    format: Literal["pdf", "docx"]


class ConvertResponse(BaseModel):
    markdown: str


def resolve_in(root: Path, requested: str, fmt: str) -> Path:
    """The real file behind `requested` if it is a regular file below `root` of the given format."""
    path = Path(requested)
    if not path.is_absolute():
        raise HTTPException(400, "path must be absolute")
    real = path.resolve()  # follows symlinks and removes ..
    if not real.is_relative_to(root.resolve()):
        raise HTTPException(400, "path is outside the artifact store")
    if not real.is_file():
        raise HTTPException(404, "file not found")
    with real.open("rb") as f:
        if f.read(len(MAGIC[fmt])) != MAGIC[fmt]:
            raise HTTPException(400, f"file is not a {fmt}")
    return real


def create_app(
    settings: Settings | None = None, limits: Limits | None = None, command: list[str] | None = None
) -> FastAPI:
    """`command` replaces the worker command; for tests."""
    settings = settings or Settings.from_env()
    settings.require("artifact_store", "service_verify")
    root = settings.artifact_store or Path()
    limits = limits or Limits.from_env()
    verifier = TokenVerifier(settings.service_verify)
    queue = asyncio.Semaphore(int(os.environ.get("SANDMAN_CONVERTER_CONCURRENCY", "1")))
    tracker = StatusTracker(settings.service_name)
    app = FastAPI(title="sandman-document-converter")
    details = lambda: {"maxPages": limits.max_pages}  # noqa: E731
    app.router.routes.append(status_route(tracker, verifier.caller_from_header, details))
    Caller = Annotated[str, Depends(require_service(verifier))]

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        if not root.is_dir():
            raise HTTPException(503, "artifact store not mounted")
        return {"status": "ok"}

    @app.post("/v1/convert")
    async def convert(request: ConvertRequest, caller: Caller) -> ConvertResponse:
        if caller != CALLER:
            raise HTTPException(403, f"only the {CALLER} may convert")
        path = resolve_in(root, request.path, request.format)
        async with queue, tracker.track():
            try:
                return ConvertResponse(markdown=await to_markdown(path, limits, command))
            except ConversionError as e:
                raise HTTPException(422, str(e)) from None

    return app
