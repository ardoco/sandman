import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from sandman_llm_replay.app import Settings, canonical, cassette_key, create_app
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads((ROOT / "contracts/conformance/vectors.json").read_text(encoding="utf-8"))["cassetteKey"]["cases"]
ENTRY_KEYS = {"key", "endpoint", "request", "response", "recordedAt"}
BODY = {"model": "openai/m", "messages": [{"role": "user", "content": "hi"}], "user": "run_1"}
REPLY = {"id": "x", "model": "m-2025", "choices": []}


class Upstream:
    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=REPLY)


def make(tmp_path: Path, mode: str, upstream: Upstream) -> TestClient:
    settings = Settings("http://gw/openai", mode, tmp_path)
    return TestClient(create_app(settings, httpx.MockTransport(upstream)))


def cassette_lines(tmp_path: Path) -> list[dict[str, object]]:
    f = tmp_path / "cassette.jsonl"
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


@pytest.mark.parametrize("case", VECTORS, ids=lambda c: c["endpoint"])
def test_vectors(case: dict[str, object]) -> None:
    request = case["request"]
    assert isinstance(request, dict)
    assert canonical(request).decode() == case["canonical"]
    assert cassette_key(request) == case["expectedKey"]


def test_user_does_not_change_key() -> None:
    assert cassette_key(BODY) == cassette_key({**BODY, "user": "other", "stream": False})


def test_live_passthrough(tmp_path: Path) -> None:
    up = Upstream()
    with make(tmp_path, "live", up) as c:
        r = c.post("/openai/chat/completions?x=1", json=BODY, headers={"Authorization": "Bearer k"})
    assert r.status_code == 200 and r.json() == REPLY
    sent = up.requests[0]
    assert str(sent.url) == "http://gw/openai/chat/completions?x=1"
    assert sent.headers["authorization"] == "Bearer k"
    assert not cassette_lines(tmp_path)


def test_record_miss_then_hit(tmp_path: Path) -> None:
    up = Upstream()
    with make(tmp_path, "record", up) as c:
        assert c.post("/openai/chat/completions", json=BODY).json() == REPLY
        (line,) = cassette_lines(tmp_path)
        assert set(line) == ENTRY_KEYS
        assert line["endpoint"] == "chat/completions" and line["key"] == cassette_key(BODY)
        assert line["request"] == BODY and line["response"] == REPLY
        assert str(line["recordedAt"]).endswith("Z")
        assert c.post("/openai/chat/completions", json={**BODY, "user": "u2"}).json() == REPLY
    assert len(up.requests) == 1
    assert len(cassette_lines(tmp_path)) == 1


def test_record_hit_from_existing_cassette(tmp_path: Path) -> None:
    with make(tmp_path, "record", Upstream()) as c:
        c.post("/openai/embeddings", json={"model": "m", "input": ["a"]})
    up = Upstream()
    with make(tmp_path, "record", up) as c:
        assert c.post("/openai/embeddings", json={"model": "m", "input": ["a"]}).status_code == 200
    assert not up.requests


def test_replay_hit_and_miss(tmp_path: Path) -> None:
    with make(tmp_path, "record", Upstream()) as c:
        c.post("/openai/chat/completions", json=BODY)
    up = Upstream()
    with make(tmp_path, "replay", up) as c:
        assert c.post("/openai/chat/completions", json=BODY).json() == REPLY
        miss = {**BODY, "model": "other"}
        r = c.post("/openai/chat/completions", json=miss)
        assert r.status_code == 404
        err = r.json()["error"]
        assert err["code"] == "replay_miss" and err["key"] == cassette_key(miss)
        assert c.get("/openai/models").status_code == 404
    assert not up.requests


@pytest.mark.parametrize("mode", ["record", "replay"])
def test_stream_rejected(tmp_path: Path, mode: str) -> None:
    up = Upstream()
    with make(tmp_path, mode, up) as c:
        r = c.post("/openai/chat/completions", json={**BODY, "stream": True})
    assert r.status_code == 400 and r.json()["error"]["code"] == "streaming_not_supported"
    assert not up.requests


def test_non_200_not_recorded(tmp_path: Path) -> None:
    with make(tmp_path, "record", Upstream(status=429)) as c:
        assert c.post("/openai/chat/completions", json=BODY).status_code == 429
    assert not cassette_lines(tmp_path)


def test_malformed_cassette_fails_startup(tmp_path: Path) -> None:
    (tmp_path / "bad.jsonl").write_text('{"key": "k"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad\.jsonl:1"):
        create_app(Settings("", "replay", tmp_path))


def test_invalid_mode_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDMAN_LLM_MODE", "bogus")
    with pytest.raises(ValueError, match="SANDMAN_LLM_MODE"):
        Settings.from_env()


def test_health_and_ready(tmp_path: Path) -> None:
    make_client: Callable[[str], TestClient] = lambda m: make(tmp_path, m, Upstream())  # noqa: E731
    for mode in ("live", "record", "replay"):
        with make_client(mode) as c:
            assert c.get("/healthz").json() == {"status": "ok"}
            assert c.get("/readyz").status_code == 200
