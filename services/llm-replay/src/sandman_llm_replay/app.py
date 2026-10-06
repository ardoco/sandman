"""OpenAI-compatible live/record/replay proxy in front of the LLM gateway (ADR-0010, D-3)."""

import asyncio
import hashlib
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import rfc8785
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

MODES = ("live", "record", "replay")
ENDPOINTS = ("chat/completions", "embeddings")
ENTRY_KEYS = ("key", "endpoint", "request", "response", "recordedAt")
EXCLUDED_MEMBERS = ("user", "stream", "stream_options")
NOT_FORWARDED = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}  # fmt: skip


@dataclass(frozen=True)
class Settings:
    upstream: str = ""
    mode: str = "live"
    records_dir: Path = Path("/llm-records")

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.environ.get("SANDMAN_LLM_MODE", "live")
        if mode not in MODES:
            raise ValueError(f"invalid SANDMAN_LLM_MODE {mode!r}, expected one of {', '.join(MODES)}")
        upstream = os.environ.get("SANDMAN_LLM_UPSTREAM", "").rstrip("/")
        if mode != "replay" and not upstream:
            raise ValueError(f"SANDMAN_LLM_UPSTREAM is required in mode {mode}")
        return cls(upstream, mode, Path(os.environ.get("SANDMAN_LLM_RECORDS_DIR", "/llm-records")))


def canonical(body: dict[str, Any]) -> bytes:
    """RFC 8785 serialization of the request body without user/stream/stream_options."""
    return rfc8785.dumps({k: v for k, v in body.items() if k not in EXCLUDED_MEMBERS})


def cassette_key(body: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(body)).hexdigest()


def load_cassettes(records_dir: Path) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for file in sorted(records_dir.glob("*.jsonl")):
        for lineno, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict) or any(k not in entry for k in ENTRY_KEYS):
                    raise ValueError(f"expected an object with members {', '.join(ENTRY_KEYS)}")
            except ValueError as e:
                raise ValueError(f"malformed cassette entry at {file}:{lineno}: {e}") from e
            entries[entry["key"]] = entry
    return entries


def error(status: int, type_: str, code: str, message: str, **extra: str) -> JSONResponse:
    return JSONResponse({"error": {"type": type_, "code": code, "message": message, **extra}}, status_code=status)


def create_app(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    mode = settings.mode
    cassette_file = settings.records_dir / "cassette.jsonl"
    lock = asyncio.Lock()
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(600.0, connect=10.0))
    if mode == "record":
        settings.records_dir.mkdir(parents=True, exist_ok=True)
    cassettes = load_cassettes(settings.records_dir) if mode != "live" and settings.records_dir.is_dir() else {}

    def log(path: str, status: int, outcome: str, key: str | None = None) -> None:
        line = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": "INFO",
            "service": "llm-replay",
            "msg": "request",
            "mode": mode,
            "path": path,
            "outcome": outcome,
            "status": status,
        }
        if key:
            line["key"] = key
        print(json.dumps(line), file=sys.stdout, flush=True)

    async def send(request: Request, path: str, body: bytes, stream: bool) -> httpx.Response:
        url = f"{settings.upstream}/{path}"
        if request.url.query:
            url += f"?{request.url.query}"
        headers = [(k, v) for k, v in request.headers.items() if k not in NOT_FORWARDED]
        upstream_request = client.build_request(request.method, url, headers=headers, content=body)
        return await client.send(upstream_request, stream=stream)

    async def passthrough(request: Request, path: str, body: bytes) -> Response:
        resp = await send(request, path, body, stream=True)
        headers = {k: v for k, v in resp.headers.items() if k not in NOT_FORWARDED | {"content-encoding"}}
        return StreamingResponse(resp.aiter_bytes(), resp.status_code, headers, background=BackgroundTask(resp.aclose))

    async def record(request: Request, path: str, body: bytes, req: dict[str, Any], key: str) -> tuple[Response, str]:
        resp = await send(request, path, body, stream=False)
        headers = {k: v for k, v in resp.headers.items() if k not in NOT_FORWARDED | {"content-encoding"}}
        out = Response(resp.content, resp.status_code, headers)
        if resp.status_code != 200:
            return out, "forwarded"
        try:
            data = resp.json()
        except ValueError:
            return out, "forwarded"
        if not isinstance(data, dict):
            return out, "forwarded"
        recorded_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        entry = {"key": key, "endpoint": path, "request": req, "response": data, "recordedAt": recorded_at}
        async with lock:
            with cassette_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        cassettes[key] = entry
        return out, "recorded"

    async def dispatch(request: Request, path: str) -> tuple[Response, str, str | None]:
        body = await request.body()
        if mode == "live" or request.method != "POST" or path not in ENDPOINTS:
            if mode == "replay":
                msg = f"{request.method} /openai/{path} is not replayable"
                return error(404, "replay_miss", "replay_miss", msg), "miss", None
            return await passthrough(request, path, body), "forwarded", None
        try:
            req = json.loads(body)
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            return (
                error(400, "invalid_request_error", "invalid_json", "request body must be a JSON object"),
                "rejected",
                None,
            )
        if req.get("stream") is True:
            msg = "streaming is not supported in record/replay mode"
            return error(400, "invalid_request_error", "streaming_not_supported", msg), "rejected", None
        key = cassette_key(req)
        if (hit := cassettes.get(key)) is not None:
            return JSONResponse(hit["response"]), "hit", key
        if mode == "replay":
            msg = f"no recorded response for key {key}"
            return error(404, "replay_miss", "replay_miss", msg, key=key), "miss", key
        out, outcome = await record(request, path, body, req, key)
        return out, outcome, key

    async def proxy(request: Request) -> Response:
        path = request.path_params["path"]
        try:
            response, outcome, key = await dispatch(request, path)
        except httpx.HTTPError as e:
            response, outcome, key = (
                error(502, "upstream_error", "upstream_unreachable", type(e).__name__),
                "forwarded",
                None,
            )
        log(path, response.status_code, outcome, key)
        return response

    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def readyz(request: Request) -> Response:
        ready = mode != "record" or (settings.records_dir.is_dir() and os.access(settings.records_dir, os.W_OK))
        return JSONResponse(
            {"status": "ok" if ready else "down"}, status_code=200 if ready else 503
        )  # shared.yaml Health

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        yield
        await client.aclose()

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/readyz", readyz),
            Route("/openai/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]),
        ],
        lifespan=lifespan,
    )


def __getattr__(name: str) -> Starlette:
    # Lazy so importing this module (tests) does not require env; uvicorn resolves `app` on demand.
    if name == "app":
        return create_app(Settings.from_env())
    raise AttributeError(name)
