"""HTTP surface: authorization, path confinement, error mapping, concurrency bound (fake worker, no Docling)."""

import asyncio
import hashlib
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sandman_agent import Settings
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
        return await self.client.post("/v1/convert", json={"path": str(path), "format": fmt}, headers=AUTH[caller])


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
    anonymous = await env.client.post("/v1/convert", json={"path": str(path), "format": "pdf"})
    assert anonymous.status_code == 401
    wrong = await env.client.post("/v1/convert", json={}, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401


async def test_path_traversal_and_outside_paths_are_rejected(env: Env, tmp_path: Path) -> None:
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")
    assert (await env.convert(outside)).status_code == 400
    assert (await env.convert(f"{env.root}/p/../../secret.pdf")).status_code == 400
    assert (await env.convert("p/a")).status_code == 400
    assert (await env.convert("/etc/passwd")).status_code == 400


async def test_symlink_escape_is_rejected(env: Env, tmp_path: Path) -> None:
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")
    link = env.root / "p" / "link"
    link.symlink_to(outside)
    assert (await env.convert(link)).status_code == 400


async def test_missing_directory_and_wrong_format(env: Env) -> None:
    assert (await env.convert(env.root / "p" / "missing")).status_code == 404
    assert (await env.convert(env.root / "p")).status_code == 404
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
    assert "exited with 3: boom" in resp.json()["detail"] and len(resp.json()["detail"]) < 400


async def test_oversized_file_is_422(tmp_path: Path) -> None:
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a").write_bytes(b"%PDF-1.4 x")
    async with make(tmp_path, OK, Limits(max_file_bytes=5)) as client:
        resp = await Env(tmp_path, client).convert(tmp_path / "p" / "a")
    assert resp.status_code == 422 and "limit is 5" in resp.json()["detail"]


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
