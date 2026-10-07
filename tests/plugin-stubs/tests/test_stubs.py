import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sandman_agent import A2aPluginClient, Contracts, PluginError, Settings
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_plugin_stubs.app import COUNT, create_detect_app, create_extract_app

MINI = Path(__file__).resolve().parents[2] / "e2e" / "fixtures" / "mini" / "s1"
CODE, TEST = "src/main/java/mini/MediaCache.java", "src/test/java/mini/MediaCacheTest.java"
SETTINGS = Settings(
    service_name="plugin-stubs", plugin_inbound_token="inbound", service_token="outbound", artifact_store=MINI
)
CONTRACTS = Contracts(DEFAULT_CONTRACTS_DIR)


def artifact(path: str, kind: str, storage: Path | None = None) -> dict[str, Any]:
    return {
        "artifactVersionId": "ver_" + "0" * 32,
        "path": path,
        "kind": kind,
        "storagePath": str(storage or MINI / path),
        "contentHash": "a" * 64,
    }


def extract_input(*artifacts: dict[str, Any]) -> dict[str, Any]:
    current = [{"path": p, "storagePath": str(MINI / p)} for p in (CODE, TEST)]
    return {
        "projectId": "mini",
        "llmPolicy": "local-only",
        "artifacts": list(artifacts),
        "context": {"currentArtifacts": current},
    }


def plugin_client() -> A2aPluginClient:
    app = create_extract_app(SETTINGS, CONTRACTS)
    transport = httpx.ASGITransport(app=app)
    return A2aPluginClient("http://plugin-stubs:8000", "inbound", CONTRACTS, poll=(0.01, 0.05), transport=transport)


async def test_extract_mini_java_files() -> None:
    out = await plugin_client().extract(extract_input(artifact(CODE, "code"), artifact(TEST, "test")))
    keys = {(e["path"], e["localKey"], e["label"], e.get("parentLocalKey")) for e in out["elements"]}
    assert keys == {
        (CODE, CODE, "CodeUnit", None),
        (CODE, "mini.MediaCache", "CodeUnit", CODE),
        (TEST, TEST, "TestCase", None),
        (TEST, "mini.MediaCacheTest", "TestCase", TEST),
    }
    test_class, code_class = (
        {"path": TEST, "localKey": "mini.MediaCacheTest"},
        {"path": CODE, "localKey": "mini.MediaCache"},
    )
    assert out["relations"] == [{"type": "DEPENDS_ON", "source": test_class, "target": code_class}]
    assert out["explicitReferences"] == [{"source": test_class, "referenceText": "REQ-1", "linkType": "verifies"}]


async def test_extract_resolves_imports_via_current_artifacts() -> None:
    out = await plugin_client().extract(extract_input(artifact(TEST, "test")))
    assert [r["target"]["path"] for r in out["relations"]] == [CODE]
    assert {e["path"] for e in out["elements"]} == {TEST}


async def test_extract_rejects_paths_outside_artifact_store() -> None:
    with pytest.raises(PluginError) as e:
        await plugin_client().extract(extract_input(artifact(CODE, "code", storage=Path("/etc/hosts"))))
    assert not e.value.retryable and "outside the artifact store" in str(e.value)


async def test_extract_healthz_is_public() -> None:
    transport = httpx.ASGITransport(app=create_extract_app(SETTINGS, CONTRACTS))
    async with httpx.AsyncClient(transport=transport, base_url="http://p") as c:
        assert (await c.get("/healthz")).json() == {"status": "ok"}
        assert (await c.get("/.well-known/agent-card.json")).json()["name"] == "plugin-stubs"


def fake_query_service(requests: list[httpx.Request], count: int = 7) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"columns": ["count(e)"], "rows": [[count]], "truncated": False, "elapsedMs": 1}
        )

    return httpx.MockTransport(handle)


async def detect_client(transport: httpx.MockTransport) -> httpx.AsyncClient:
    app = create_detect_app(SETTINGS, CONTRACTS, transport=transport)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://plugin-stubs:8001")


DETECT = {"projectId": "mini", "llmPolicy": "local-only", "queryServiceUrl": "http://query-service:8000/"}
INBOUND = {"Authorization": "Bearer inbound"}


async def test_detect_counts_elements_via_query_service_with_own_token() -> None:
    requests: list[httpx.Request] = []
    async with await detect_client(fake_query_service(requests)) as c:
        resp = await c.post("/v1/detect", json=DETECT, headers=INBOUND)
    assert resp.status_code == 200
    out = resp.json()
    CONTRACTS.validate("skills.schema.json#/$defs/PluginDetectOutput", out)
    assert out["findings"] == [] and "7 elements" in out["warnings"][0]
    [req] = requests
    assert str(req.url) == "http://query-service:8000/v1/query/cypher"
    assert req.headers["authorization"] == "Bearer outbound"
    assert json.loads(req.content) == {"projectId": "mini", "cypher": COUNT}


async def test_detect_auth_validation_and_manifest() -> None:
    requests: list[httpx.Request] = []
    async with await detect_client(fake_query_service(requests)) as c:
        assert (await c.post("/v1/detect", json=DETECT)).status_code == 401
        assert (await c.post("/v1/detect", json=DETECT, headers={"Authorization": "Bearer x"})).status_code == 401
        assert (await c.post("/v1/detect", json={"projectId": "mini"}, headers=INBOUND)).status_code == 400
        assert (await c.get("/manifest")).status_code == 401
        manifest = (await c.get("/manifest", headers=INBOUND)).json()
        assert manifest["id"] == "plugin-stubs" and manifest["operations"] == ["detect"]
        assert (await c.get("/healthz")).json() == {"status": "ok"}
    assert requests == []


async def test_detect_query_service_failure_is_503() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(403, json={"title": "Forbidden"}))
    async with await detect_client(transport) as c:
        resp = await c.post("/v1/detect", json=DETECT, headers=INBOUND)
    assert resp.status_code == 503 and resp.headers["content-type"] == "application/problem+json"


async def test_detect_rejects_foreign_query_service_url_without_outbound_call() -> None:
    requests: list[httpx.Request] = []
    async with await detect_client(fake_query_service(requests)) as c:
        resp = await c.post("/v1/detect", json={**DETECT, "queryServiceUrl": "http://evil:8000"}, headers=INBOUND)
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert requests == []


async def test_detect_uses_configured_query_service_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDMAN_QUERY_SERVICE_URL", "http://qs.internal:9000")
    requests: list[httpx.Request] = []
    async with await detect_client(fake_query_service(requests)) as c:
        hint = {**DETECT, "queryServiceUrl": "http://QS.internal:9000/"}
        assert (await c.post("/v1/detect", json=hint, headers=INBOUND)).status_code == 200
        assert (await c.post("/v1/detect", json=DETECT, headers=INBOUND)).status_code == 400
    [req] = requests
    assert str(req.url) == "http://qs.internal:9000/v1/query/cypher"
