"""T0.4 gateway checks that need virtual keys (2, 3, 4, 6, 12) against the running llm-gateway.

Never prints keys. Run from the repository root with the gateway up (`make infra` or
`docker compose -f deploy/docker-compose.yml up -d llm-gateway`):

    uv run python spikes/T0.4/gateway_checks.py

Environment (defaults in brackets):
    GW_URL            [http://127.0.0.1:8081]
    KEY_FILE          [deploy/secrets/llm_key_query]          cloud-allowed virtual key
    LOCAL_KEY_FILE    [deploy/secrets/llm_key_query_local]    Ollama-only virtual key
    ADMIN_PW_FILE     [deploy/secrets/gateway_admin_password]
    CHAT_MODEL        [openai/gpt-4.1-nano]   smallest OpenAI chat model you allow
    EMBED_MODEL       [openai/text-embedding-3-small]
    OPENROUTER_MODEL  [openrouter/openai/gpt-4.1-nano]
    OLLAMA_MODEL      [ollama/qwen3:0.6b]     only if SELFHOSTED_LLM_URL is configured
Budget exhaustion (check 5) and rate limits are manual: see README.md.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from openai import APIStatusError, OpenAI

GW = os.environ.get("GW_URL", "http://127.0.0.1:8081")
results: list[tuple[str, str]] = []


def secret(env: str, default: str) -> str:
    return Path(os.environ.get(env, default)).read_text().strip()


def record(check: str, ok: bool | None, evidence: str) -> None:
    verdict = "PASS" if ok else ("FAIL" if ok is False else "INFO")
    results.append((check, verdict))
    print(f"[{verdict}] {check}: {evidence}", flush=True)


def attempt(check: str, fn) -> None:  # type: ignore[no-untyped-def]
    try:
        record(check, True, str(fn())[:160])
    except Exception as e:  # noqa: BLE001 - report every failure and continue
        record(check, False, f"{type(e).__name__}: {str(e)[:160]}")


def main() -> None:
    try:
        urllib.request.urlopen(f"{GW}/health", timeout=5).close()
    except OSError as e:
        sys.exit(f"gateway not reachable at {GW} ({e}); start it: docker compose -f deploy/docker-compose.yml up -d llm-gateway")
    key = secret("KEY_FILE", "deploy/secrets/llm_key_query")
    local_key = secret("LOCAL_KEY_FILE", "deploy/secrets/llm_key_query_local")
    chat_model = os.environ.get("CHAT_MODEL", "openai/gpt-4.1-nano")
    embed_model = os.environ.get("EMBED_MODEL", "openai/text-embedding-3-small")
    client = OpenAI(base_url=f"{GW}/openai", api_key=key, max_retries=0)
    hello = [{"role": "user", "content": "Reply with the single word: ok"}]
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    attempt("2 inference with virtual key", lambda: client.chat.completions.create(
        model=chat_model, messages=hello, max_tokens=5, extra_headers={"traceparent": traceparent}).model)
    attempt("3 tool calling", lambda: client.chat.completions.create(
        model=chat_model, max_tokens=50, messages=[{"role": "user", "content": "What is the weather in Karlsruhe?"}],
        tools=[{"type": "function", "function": {"name": "weather", "parameters": {
            "type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}],
        tool_choice="required").choices[0].message.tool_calls[0].function.arguments)
    attempt("3 structured output (json_schema)", lambda: json.loads(client.chat.completions.create(
        model=chat_model, max_tokens=30, messages=[{"role": "user", "content": "Give answer yes."}],
        response_format={"type": "json_schema", "json_schema": {"name": "a", "strict": True, "schema": {
            "type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"],
            "additionalProperties": False}}}).choices[0].message.content))
    attempt("3 streaming", lambda: "".join(
        c.choices[0].delta.content or "" for c in client.chat.completions.create(
            model=chat_model, messages=hello, max_tokens=5, stream=True) if c.choices))
    attempt("3 embeddings", lambda: len(client.embeddings.create(model=embed_model, input=["MediaCache"]).data[0].embedding))
    attempt("4 OpenRouter route", lambda: client.chat.completions.create(
        model=os.environ.get("OPENROUTER_MODEL", "openrouter/openai/gpt-4.1-nano"), messages=hello, max_tokens=5).model)
    if os.environ.get("OLLAMA_MODEL"):
        attempt("4 Ollama route", lambda: client.chat.completions.create(
            model=os.environ["OLLAMA_MODEL"], messages=hello, max_tokens=5).model)

    local = OpenAI(base_url=f"{GW}/openai", api_key=local_key, max_retries=0)
    try:
        local.chat.completions.create(model=chat_model, messages=hello, max_tokens=5)
        record("12 local-only key rejected on cloud route", False, "cloud call succeeded with the -local key")
    except APIStatusError as e:  # only a gateway refusal counts, not connection errors
        record("12 local-only key rejected on cloud route", e.status_code in (400, 401, 403, 404),
               f"HTTP {e.status_code}: {str(e)[:120]}")
    except Exception as e:  # noqa: BLE001
        record("12 local-only key rejected on cloud route", False, f"{type(e).__name__}: {str(e)[:120]}")

    admin = base64.b64encode(f"admin:{secret('ADMIN_PW_FILE', 'deploy/secrets/gateway_admin_password')}".encode()).decode()
    for path in ("/metrics", "/api/logs?limit=5"):
        req = urllib.request.Request(GW + path, headers={"Authorization": f"Basic {admin}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode()
            if path == "/metrics":
                names = sorted({line.split("{")[0].split(" ")[0] for line in body.splitlines() if line.startswith("bifrost_")})
                record("6/V-36 metrics with admin auth", True, f"{len(names)} bifrost_* metrics: {', '.join(names[:12])}")
            else:
                record("6 request log via management API", True, f"{len(body)} bytes; traceparent present: {traceparent in body}")
        except urllib.error.HTTPError as e:
            record(f"6 {path}", False, f"HTTP {e.code}")
        except OSError as e:
            record(f"6 {path}", False, f"{type(e).__name__}: {e}")
    print("\nSUMMARY " + " ".join(f"{c}={v}" for c, v in results))


if __name__ == "__main__":
    sys.exit(main())
