"""HTTP surface: authorization, path confinement, error mapping, concurrency bound (fake worker, no Docling)."""

import asyncio
import hashlib
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import Request
from sandman_agent import ConfigError, Settings
from sandman_document_converter.app import create_app
from sandman_document_converter.convert import Limits

TOKENS = {"extraction-agent": "ea-token", "dashboard": "dash-token", "query-service": "qs-token"}
AUTH = {name: {"Authorization": f"Bearer {t}"} for name, t in TOKENS.items()}
OK = [sys.executable, "-c", "print('# T\\n\\nHello.')"]


def fake(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class Env:
    def __init__(self, root: Path, client: httpx.AsyncClient) -> None:
        self.root, self.client = root, client

    async def convert(self, path: str | Path, fmt: str = "pdf", caller: str = "extraction-agent") -> httpx.Response:
        rel = path.relative_to(self.root) if isinstance(path, Path) and path.is_relative_to(self.root) else path
        return await self.client.post("/v1/convert", json={"path": str(rel), "format": fmt}, headers=AUTH[caller])


def make(root: Path, command: list[str], limits: Limits | None = None) -> httpx.AsyncClient:
    settings = Settings(
        service_name="document-converter",
        artifact_store=root,
        service_verify={n: hashlib.sha256(t.encode()).hexdigest() for n, t in TOKENS.items()},
    )
    app = create_app(settings, limits, command)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://converter")


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[Env]:
    root = tmp_path / "store"
    (root / "p").mkdir(parents=True)
    async with make(root, OK) as client:
        yield Env(root, client)


def blob(env: Env, name: str = "a", data: bytes = b"%PDF-1.4 x") -> Path:
    path = env.root / "p" / name
    path.write_bytes(data)
    return path


async def test_convert(env: Env) -> None:
    resp = await env.convert(blob(env))
    assert resp.status_code == 200
    assert resp.json() == {"markdown": "# T\n\nHello.\n"}


async def test_only_the_extraction_agent_converts(env: Env) -> None:
    path = blob(env)
    assert (await env.convert(path, caller="dashboard")).status_code == 403
    assert (await env.convert(path, caller="query-service")).status_code == 403
    anonymous = await env.client.post("/v1/convert", json={"path": "p/a", "format": "pdf"})
    assert anonymous.status_code == 401
    wrong = await env.client.post("/v1/convert", json={}, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401


async def test_path_traversal_and_outside_paths_are_rejected(env: Env, tmp_path: Path) -> None:
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")
    assert (await env.convert(outside)).status_code == 400
    assert (await env.convert("p/../../secret.pdf")).status_code == 400
    assert (await env.convert("/etc/passwd")).status_code == 400
    assert (await env.convert(f"{env.root}/p/a")).status_code == 400


async def test_symlink_escape_is_rejected(env: Env, tmp_path: Path) -> None:
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")
    link = env.root / "p" / "link"
    link.symlink_to(outside)
    assert (await env.convert(link)).status_code == 400


async def test_missing_directory_and_wrong_format(env: Env) -> None:
    assert (await env.convert(env.root / "p" / "missing")).status_code == 404
    assert (await env.convert(env.root / "p")).status_code == 400
    assert (await env.convert(blob(env, "docx", b"PK\x03\x04 x"), fmt="pdf")).status_code == 400
    assert (await env.convert(blob(env, "pdf"), fmt="docx")).status_code == 400
    assert (await env.convert(blob(env, "ok"), fmt="txt")).status_code == 422


async def test_conversion_errors_are_422_with_bounded_detail(tmp_path: Path) -> None:
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a").write_bytes(b"%PDF-1.4 x")
    code = "import sys; sys.stderr.write('x' * 5000 + '\\nboom'); sys.exit(3)"
    async with make(tmp_path, fake(code)) as client:
        resp = await Env(tmp_path, client).convert(tmp_path / "p" / "a")
    assert resp.status_code == 422
    assert resp.json()["detail"] == "conversion failed: worker failed"


async def test_oversized_file_is_422(tmp_path: Path) -> None:
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a").write_bytes(b"%PDF-1.4 x")
    async with make(tmp_path, OK, Limits(max_file_bytes=5)) as client:
        resp = await Env(tmp_path, client).convert(tmp_path / "p" / "a")
    assert resp.status_code == 422 and resp.json()["detail"] == "conversion failed: file too large"


async def test_conversions_are_serialized(tmp_path: Path) -> None:
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a").write_bytes(b"%PDF-1.4 x")
    marker = tmp_path / "m"
    code = (
        f"import os, time; m = {str(marker)!r}; assert not os.path.exists(m); open(m, 'w').close(); "
        "time.sleep(0.4); os.remove(m); print('ok')"
    )
    async with make(tmp_path, fake(code)) as client:
        env = Env(tmp_path, client)
        results = await asyncio.gather(*(env.convert(tmp_path / "p" / "a") for _ in range(3)))
    assert [r.status_code for r in results] == [200, 200, 200]


async def test_health_and_status(env: Env) -> None:
    assert (await env.client.get("/healthz")).json() == {"status": "ok"}
    assert (await env.client.get("/readyz")).status_code == 200
    assert (await env.client.get("/v1/status")).status_code == 401
    status = await env.client.get("/v1/status", headers=AUTH["dashboard"])
    assert status.status_code == 200
    assert status.json()["service"] == "document-converter" and status.json()["state"] == "idle"


async def test_not_ready_without_artifact_store(tmp_path: Path) -> None:
    async with make(tmp_path / "missing", OK) as client:
        assert (await client.get("/readyz")).status_code == 503


async def test_nul_byte_and_malformed_paths_are_400(env: Env) -> None:
    assert (await env.convert("p/a\x00.pdf")).status_code == 400
    assert (await env.convert("p/" + "a" * 300)).status_code in (400, 404)


async def test_fifo_is_rejected_without_blocking(env: Env) -> None:
    fifo = env.root / "p" / "fifo"
    os.mkfifo(fifo)
    resp = await asyncio.wait_for(env.convert(fifo), 5)
    assert resp.status_code == 400 and "regular" in resp.json()["detail"]


async def test_symlink_at_the_last_component_inside_the_store_is_resolved_not_followed_later(env: Env) -> None:
    target = blob(env, "real")
    (env.root / "p" / "alias").symlink_to(target)
    assert (await env.convert(env.root / "p" / "alias")).status_code == 200


async def test_file_swapped_after_validation_is_not_read(tmp_path: Path) -> None:
    root = tmp_path / "store"
    (root / "p").mkdir(parents=True)
    victim = root / "p" / "a"
    victim.write_bytes(b"%PDF-1.4 original")
    secret = tmp_path / "secret"
    secret.write_text("SECRET")
    code = "import sys, time; time.sleep(0.5); print(open(sys.argv[1], 'rb').read().decode())"
    async with make(root, fake(code)) as client:
        env = Env(root, client)

        async def swap() -> None:
            await asyncio.sleep(0.2)
            victim.unlink()
            victim.symlink_to(secret)

        resp, _ = await asyncio.gather(env.convert(victim), swap())
    assert resp.status_code == 200 and "original" in resp.json()["markdown"] and "SECRET" not in resp.text


async def test_full_queue_answers_503_with_retry_after(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDMAN_CONVERTER_MAX_QUEUE", "1")
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a").write_bytes(b"%PDF-1.4 x")
    async with make(tmp_path, fake("import time; time.sleep(0.6); print('ok')")) as client:
        env = Env(tmp_path, client)
        results = await asyncio.gather(*(env.convert(tmp_path / "p" / "a") for _ in range(4)))
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 200, 503, 503]
    assert all(r.headers["retry-after"] for r in results if r.status_code == 503)


async def test_disconnected_client_is_not_converted(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    async def gone(self: Request) -> bool:
        return True

    monkeypatch.setattr(Request, "is_disconnected", gone)
    assert (await env.convert(blob(env))).status_code == 503


@pytest.mark.parametrize("name,value", [("SANDMAN_CONVERTER_CONCURRENCY", "0"), ("SANDMAN_CONVERTER_CONCURRENCY", "x"),
                                        ("SANDMAN_CONVERTER_MAX_QUEUE", "-1")])  # fmt: skip
def test_invalid_bounds_fail_at_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        make(tmp_path, OK)
