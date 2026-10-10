"""V-42: Bifrost custom OpenAI-compatible provider with a base URL (vLLM, institutional server) against a stub.

Starts a stdlib stub server and a throwaway Bifrost container (image from deploy/docker-compose.yml, no secrets) with the
example of deploy/gateway/config.json, then checks chat, embeddings, key forwarding and tolerance of the `_comment*`/`_example*` keys.

    DOCKER_HOST=unix://$HOME/.docker/run/docker.sock uv run python spikes/T0.4/custom_provider_check.py
"""

import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parents[2]
KEY = "sk-stub-test"
seen: list[tuple[str, str]] = []
bodies: list[dict] = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        seen.append((self.path, self.headers.get("Authorization", "")))
        bodies.append(body)
        if self.path.endswith("/chat/completions"):
            reply = {"id": "c1", "object": "chat.completion", "created": 1, "model": body["model"], "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        elif self.path.endswith("/embeddings"):
            reply = {"object": "list", "model": body["model"], "usage": {"prompt_tokens": 1, "total_tokens": 1},
                     "data": [{"object": "embedding", "index": 0, "embedding": [0.25, 0.5]}]}
        else:
            self.send_error(404)
            return
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def post(url: str, path: str, body: dict, headers: dict | None = None) -> dict:
    req = urllib.request.Request(f"{url}{path}", json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:300]}") from e


def check_passthrough(url: str) -> None:
    """R-041: the openrouter pin must reach the upstream as `provider`; llm-replay sends it as extra_params.provider on /openai/v1 plus the passthrough header."""
    pin = {"order": ["deepinfra"], "allow_fallbacks": False}
    msgs = [{"role": "user", "content": "ping"}]
    hdr = {"x-bf-passthrough-extra-params": "true"}
    model = "openrouter/meta-llama/x"
    cases = {  # label: (path, body extras, headers, forwarded?)
        "/openai/v1 extra_params.provider + header": ("/openai/v1/chat/completions", {"extra_params": {"provider": pin}}, hdr, True),
        "/openai/v1 extra_params.provider, no header": ("/openai/v1/chat/completions", {"extra_params": {"provider": pin}}, {}, False),
        "/openai/v1 top-level provider + header": ("/openai/v1/chat/completions", {"provider": pin}, hdr, False),
        "/v1 top-level provider + header": ("/v1/chat/completions", {"provider": pin}, hdr, True),
        "/v1 top-level provider, no header": ("/v1/chat/completions", {"provider": pin}, {}, False),
    }
    for label, (path, extra, headers, forwarded) in cases.items():
        n = len(bodies)
        post(url, path, {"model": model, "messages": msgs, **extra}, headers)
        got = bodies[n:]
        assert len(got) == 1, (label, got)
        assert got[0].get("provider") == (pin if forwarded else None), (label, got[0])
        print(f"[INFO] {label}: provider {'forwarded' if forwarded else 'dropped'}")
    print("[PASS] R-041 passthrough: pin reaches openrouter upstream via extra_params + header on /openai/v1 (or top-level + header on /v1)")


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    deployed = json.loads((ROOT / "deploy/gateway/config.json").read_text())
    example = deployed["_example_custom_provider"]
    config = {k: v for k, v in deployed.items() if k.startswith(("_", "$"))}
    openrouter = json.loads(json.dumps(example["provider"]))
    openrouter.pop("custom_provider_config")
    openrouter["keys"][0] |= {"name": "or-1"}
    example["provider"]["network_config"]["base_url"] = f"http://host.docker.internal:{server.server_port}"
    openrouter["network_config"]["base_url"] = example["provider"]["network_config"]["base_url"]
    config |= {"client": {"enforce_auth_on_inference": False}, "providers": {example["name"]: example["provider"], "openrouter": openrouter}}
    image = re.search(r"image: (maximhq/bifrost:\S+)", (ROOT / "deploy/docker-compose.yml").read_text()).group(1)
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.json"
        cfg.write_text(json.dumps(config))
        cfg.chmod(0o644)
        cid = subprocess.run(
            ["docker", "run", "-d", "--rm", "-p", "127.0.0.1::8080", "--add-host=host.docker.internal:host-gateway",
             "-e", "APP_HOST=0.0.0.0", "-e", "APP_PORT=8080",
             "-e", f"CUSTOM_LLM_KEY={KEY}",
             "-v", f"{cfg}:/app/data/config.json:ro", image],
            check=True, capture_output=True, text=True).stdout.strip()
        try:
            port = subprocess.run(["docker", "port", cid, "8080/tcp"], check=True, capture_output=True,
                                  text=True).stdout.split(":")[-1].strip()
            url = f"http://127.0.0.1:{port}"
            for _ in range(60):
                try:
                    urllib.request.urlopen(f"{url}/health", timeout=2).close()
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(1)
            name = example["name"]
            chat = post(url, "/openai/v1/chat/completions",
                        {"model": f"{name}/qwen3:4b", "messages": [{"role": "user", "content": "ping"}]})
            assert chat["choices"][0]["message"]["content"] == "pong", chat
            emb = post(url, "/openai/v1/embeddings", {"model": f"{name}/nomic-embed-text", "input": "x"})
            assert emb["data"][0]["embedding"] == [0.25, 0.5], emb
            assert [a for _, a in seen] == [f"Bearer {KEY}"] * 2, seen
            assert [p for p, _ in seen] == ["/v1/chat/completions", "/v1/embeddings"], seen
            print(f"[PASS] V-42 custom provider {name!r} via base_url: chat, embeddings, key forwarded ({image})")
            check_passthrough(url)
            return 0
        except Exception as e:
            print(f"[FAIL] V-42: {type(e).__name__}: {e}")
            print(subprocess.run(["docker", "logs", "--tail", "60", cid], capture_output=True, text=True).stdout)
            return 1
        finally:
            subprocess.run(["docker", "stop", cid], capture_output=True)
            server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
