"""Bifrost's `ollama` provider reaches an Open WebUI style proxy (`https://<host>/ollama`, bearer API key)."""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from testcontainers.core.container import DockerContainer

COMPOSE = Path(__file__).parents[2] / "deploy" / "docker-compose.yml"
GATEWAY_CONFIG = Path(__file__).parents[2] / "deploy" / "gateway" / "config.json"
KEY = "sk-open-webui-test"
PREFIX = "/ollama"


class FakeOpenWebUi(BaseHTTPRequestHandler):
    seen: list[tuple[str, str]] = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        type(self).seen.append((self.path, self.headers.get("Authorization", "")))
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            return self._reply(401, {"detail": "Not authenticated"})
        if self.path == f"{PREFIX}/v1/chat/completions":
            return self._reply(
                200,
                {
                    "id": "c1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        if self.path == f"{PREFIX}/v1/embeddings":
            return self._reply(
                200,
                {
                    "object": "list",
                    "model": body["model"],
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.25, 0.5]}],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
        self._reply(404, {"detail": "Not Found"})

    def _reply(self, status: int, payload: dict):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def gateway_image() -> str:
    return re.search(r"image: (maximhq/bifrost:\S+)", COMPOSE.read_text()).group(1)


@pytest.fixture(params=[True, False], ids=["configured", "empty-url"])
def gateway(tmp_path, request):
    FakeOpenWebUi.seen = []
    server = ThreadingHTTPServer(("0.0.0.0", 0), FakeOpenWebUi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    deployed = json.loads(GATEWAY_CONFIG.read_text())
    config = {
        "client": {"enforce_auth_on_inference": False},
        "providers": {"ollama": deployed["providers"]["ollama"]},
    }
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config))
    cfg.chmod(0o644)
    container = (
        DockerContainer(gateway_image())
        .with_env("APP_HOST", "0.0.0.0")
        .with_env("APP_PORT", "8080")
        .with_env(
            "SELFHOSTED_LLM_URL",
            f"http://host.docker.internal:{server.server_port}{PREFIX}" if request.param else "",
        )
        .with_env("SELFHOSTED_LLM_KEY", KEY)
        .with_volume_mapping(str(cfg), "/app/data/config.json", "ro")
        .with_exposed_ports(8080)
        .with_kwargs(extra_hosts={"host.docker.internal": "host-gateway"})
    )
    try:
        container.start()
    except Exception as e:
        server.shutdown()
        pytest.skip(f"docker unavailable: {e}")
    url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8080)}"
    for _ in range(60):
        try:
            if httpx.get(f"{url}/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    yield url if request.param else url + "#empty"
    container.stop()
    server.shutdown()


def test_empty_url_fails_closed(gateway):
    if not gateway.endswith("#empty"):
        pytest.skip("configured case")
    gateway = gateway.removesuffix("#empty")
    for path, body in (
        ("chat/completions", {"model": "ollama/qwen3:1.7b", "messages": [{"role": "user", "content": "x"}]}),
        ("embeddings", {"model": "ollama/nomic-embed-text:v1.5", "input": "x"}),
    ):
        assert httpx.post(f"{gateway}/openai/v1/{path}", json=body, timeout=30).status_code >= 400
    assert FakeOpenWebUi.seen == []


def test_chat_and_embeddings_through_open_webui_proxy(gateway):
    if gateway.endswith("#empty"):
        pytest.skip("empty-url case")
    chat = httpx.post(
        f"{gateway}/openai/v1/chat/completions",
        json={"model": "ollama/qwen3:1.7b", "messages": [{"role": "user", "content": "ping"}]},
        timeout=30,
    )
    assert chat.status_code == 200, chat.text
    assert chat.json()["choices"][0]["message"]["content"] == "pong"

    emb = httpx.post(
        f"{gateway}/openai/v1/embeddings",
        json={"model": "ollama/nomic-embed-text:v1.5", "input": "hello"},
        timeout=30,
    )
    assert emb.status_code == 200, emb.text
    assert emb.json()["data"][0]["embedding"] == [0.25, 0.5]

    assert FakeOpenWebUi.seen == [
        (f"{PREFIX}/v1/chat/completions", f"Bearer {KEY}"),
        (f"{PREFIX}/v1/embeddings", f"Bearer {KEY}"),
    ]
