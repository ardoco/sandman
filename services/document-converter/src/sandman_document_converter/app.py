"""document-converter: POST /v1/convert turns a PDF/DOCX of the (read-only mounted) artifact store into Markdown.

Run: uvicorn --factory sandman_document_converter.app:create_app --host 0.0.0.0 --port 8000
Only the extraction-agent may convert; every service token may read /v1/status. `path` is relative to the artifact
store root. The file is opened once (no symlink at the last component, regular files only) and the worker reads that
descriptor. At most SANDMAN_CONVERTER_CONCURRENCY conversions run and SANDMAN_CONVERTER_MAX_QUEUE more wait; beyond
that the answer is 503 with Retry-After.
"""

import asyncio
import logging
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field
from sandman_agent import ConfigError, Settings, StatusTracker, TokenVerifier, require_service, status_route
from sandman_agent.telemetry import FASTAPI_TELEMETRY, setup_logging
from starlette.exceptions import HTTPException

from sandman_document_converter.convert import ConversionError, Limits, to_markdown

log = logging.getLogger(__name__)

CALLER = "extraction-agent"
MAGIC = {"pdf": b"%PDF-", "docx": b"PK\x03\x04"}
RETRY_AFTER = "10"


class ConvertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    format: Literal["pdf", "docx"]


class ConvertResponse(BaseModel):
    markdown: str


def open_in(root: Path, relative: str, fmt: str) -> int:
    """Descriptor of the regular file `relative` below `root` if it has the magic bytes of `fmt`; caller closes it."""
    if "\x00" in relative or Path(relative).is_absolute():
        raise HTTPException(400, "path must be relative to the artifact store")
    try:
        real = (root / relative).resolve(strict=True)  # follows symlinks and removes ..
    except FileNotFoundError:
        raise HTTPException(404, "file not found") from None
    except (OSError, ValueError):
        raise HTTPException(400, "invalid path") from None
    if not real.is_relative_to(root.resolve()):
        raise HTTPException(400, "path is outside the artifact store")
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        raise HTTPException(400, "file cannot be opened") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise HTTPException(400, "not a regular file")
        if os.pread(fd, len(MAGIC[fmt]), 0) != MAGIC[fmt]:
            raise HTTPException(400, f"file is not a {fmt}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def env_int(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    return value


def create_app(
    settings: Settings | None = None, limits: Limits | None = None, command: list[str] | None = None
) -> FastAPI:
    """`command` replaces the worker command; for tests."""
    settings = settings or Settings.from_env()
    settings.require("artifact_store", "service_verify")
    root = settings.artifact_store or Path()
    limits = limits or Limits.from_env()
    verifier = TokenVerifier(settings.service_verify)
    slots = asyncio.Semaphore(env_int("SANDMAN_CONVERTER_CONCURRENCY", 1, 1))
    max_waiting = env_int("SANDMAN_CONVERTER_MAX_QUEUE", 4, 0)
    waiting = 0
    tracker = StatusTracker(settings.service_name)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(settings.service_name, settings.log_file)
        yield

    app = FastAPI(title="sandman-document-converter", lifespan=lifespan, telemetry=FASTAPI_TELEMETRY)
    details = lambda: {"maxPages": limits.max_pages, "waiting": waiting}  # noqa: E731
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
    async def convert(body: ConvertRequest, caller: Caller, request: Request) -> ConvertResponse:
        nonlocal waiting
        if caller != CALLER:
            raise HTTPException(403, f"only the {CALLER} may convert")
        fd = open_in(root, body.path, body.format)
        try:
            if slots.locked() and waiting >= max_waiting:
                raise HTTPException(503, "converter is busy", headers={"Retry-After": RETRY_AFTER})
            waiting += 1
            try:
                await slots.acquire()
            finally:
                waiting -= 1
            try:
                if await request.is_disconnected():
                    raise HTTPException(503, "client disconnected")
                async with tracker.track():
                    return ConvertResponse(markdown=await to_markdown(Path(body.path), limits, command, fd))
            finally:
                slots.release()
        except ConversionError as e:
            log.warning("conversion failed", extra={"fields": {"path": body.path, "error": str(e)}})
            raise HTTPException(422, f"conversion failed: {e.reason}") from None
        finally:
            os.close(fd)

    return app
