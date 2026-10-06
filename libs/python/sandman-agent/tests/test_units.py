import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sandman_agent import auth, events
from sandman_agent.artifacts import ArtifactStore
from sandman_agent.config import ConfigError, Settings
from sandman_agent.contracts import ContractError, Contracts
from sandman_agent.llm import ChatMessage, ChatRequest, ModelSelector, PolicyError
from starlette.exceptions import HTTPException

EVENT_DATA = {
    "projectId": "mini",
    "sourceVersionIds": [],
    "targetVersionIds": [],
    "runs": [],
}


def test_settings_read_file_secrets_and_report_missing(tmp_path: Path) -> None:
    (tmp_path / "pw").write_text("s3cret\n")
    (tmp_path / "verify.json").write_text(json.dumps({"orchestrator": "ab"}))
    s = Settings.from_env(
        {
            "SANDMAN_SERVICE_NAME": "tlr-agent",
            "SANDMAN_KG_PASSWORD_FILE": str(tmp_path / "pw"),
            "SANDMAN_SERVICE_VERIFY_FILE": str(tmp_path / "verify.json"),
        }
    )
    assert s.kg_password == "s3cret" and s.service_verify == {"orchestrator": "ab"}
    with pytest.raises(ConfigError, match="SANDMAN_KG_URI, SANDMAN_NATS_URL"):
        s.require("kg_uri", "nats_url", "kg_password")
    with pytest.raises(ConfigError, match="SANDMAN_SERVICE_NAME"):
        Settings.from_env({})


def test_model_selector_enforces_local_only(tmp_path: Path) -> None:
    models = tmp_path / "models.yaml"
    models.write_text("routes: {chat-default: openai/m, chat-local: ollama/m:1}\n")
    selector = ModelSelector(models, "http://llm-replay:8000/openai", "cloud-key", "local-key")
    local = selector.client("local-only")
    assert local.api_key == "local-key" and selector.chat_model("local-only") == "chat-local"
    assert selector.client("cloud-allowed").api_key == "cloud-key"
    with pytest.raises(PolicyError):
        local._model("chat-default")
    with pytest.raises(PolicyError):
        ModelSelector(models, "u", "k", None).client("local-only")
    assert local._model("chat-local") == "ollama/m:1"
    assert ChatRequest("chat-local", [ChatMessage("user", "hi")]).temperature == 0.0


def test_token_verification_and_forwarders() -> None:
    verifier = auth.TokenVerifier({"query-service": hashlib.sha256(b"qs").hexdigest()})
    assert verifier.caller_from_header("Bearer qs") == "query-service"
    assert verifier.caller_from_header("Bearer other") is None and verifier.caller(None) is None
    assert auth.trusted_actor("query-service", {"x-sandman-actor": "user:bob"}) == "user:bob"
    with pytest.raises(HTTPException) as e:
        auth.trusted_actor("tlr-agent", {"x-sandman-actor": "user:bob"})
    assert e.value.status_code == 403
    assert auth.actor_from_metadata("orchestrator", {"sandman.actor": "user:bob"}) == "user:bob"
    assert auth.actor_from_metadata("tlr-agent", {"sandman.actor": "user:bob"}) is None


async def test_require_service_rejects_plugins_unless_allowed() -> None:
    verifier = auth.TokenVerifier({"plugin:x": hashlib.sha256(b"p").hexdigest()})
    request: Any = SimpleNamespace(headers={"authorization": "Bearer p"})
    with pytest.raises(HTTPException) as e:
        await auth.require_service(verifier)(request)
    assert e.value.status_code == 403
    assert await auth.require_service(verifier, allow_plugins=True)(request) == "plugin:x"


class FlakyJs:
    def __init__(self, failures: int) -> None:
        self.failures, self.calls = failures, 0

    async def publish(self, subject: str, payload: bytes, headers: dict[str, str]) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("nats down")


async def test_publisher_retries_then_raises(contracts: Contracts) -> None:
    js: Any = FlakyJs(failures=2)
    publisher = events.EventPublisher(js, contracts, "tlr-agent", backoff=(0, 0, 0))
    event = await publisher.publish("sandman.tracelinks.updated", "mini", EVENT_DATA, causation_id="m-1")
    assert js.calls == 3 and event.source == "sandman/tlr-agent"
    failing: Any = FlakyJs(failures=10)
    with pytest.raises(events.PublishError):
        await events.EventPublisher(failing, contracts, "x", backoff=(0, 0, 0)).publish(
            "sandman.tracelinks.updated", "mini", EVENT_DATA, causation_id="m-1"
        )
    assert failing.calls == 4
    with pytest.raises(ContractError):  # completion event without causationid
        await publisher.publish("sandman.tracelinks.updated", "mini", EVENT_DATA)


class FakeMsg:
    def __init__(self, data: bytes, delivered: int) -> None:
        self.data, self.metadata = data, SimpleNamespace(num_delivered=delivered)
        self.acked = False
        self.nak_delay: float | None = None

    async def ack(self) -> None:
        self.acked = True

    async def nak(self, delay: float | None = None) -> None:
        self.nak_delay = delay


async def test_consumer_acks_on_success_and_naks_with_backoff(contracts: Contracts) -> None:
    event = events.build_event(contracts, "x", "sandman.tracelinks.updated", "mini", EVENT_DATA, None, "m-1", None)
    payload = json.dumps(event.to_json()).encode()
    seen: list[events.Event] = []

    async def ok(e: events.Event) -> None:
        seen.append(e)

    async def boom(e: events.Event) -> None:
        raise RuntimeError("handler failed")

    good = FakeMsg(payload, 1)
    await events.handle_message(good, ok)
    assert good.acked and seen[0].id == event.id
    bad = FakeMsg(payload, 2)
    await events.handle_message(bad, boom)
    assert not bad.acked and bad.nak_delay == events.CONSUMER_BACKOFF[1]


def test_artifact_store_is_content_addressed(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    sha, uri = store.put("mini", b"hello")
    assert uri == f"artifact://mini/{sha}" and store.put("mini", b"hello") == (sha, uri)
    with store.open(uri) as f:
        assert f.read() == b"hello"
    for bad in ("artifact://mini/../../etc", "artifact://Mini/" + sha):
        with pytest.raises(ValueError):
            store.path_for(bad)
    with pytest.raises(ValueError):
        store.put("../x", b"")
