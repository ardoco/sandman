import asyncio
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sandman_agent import events, telemetry
from sandman_agent.a2a import A2aClient, SkillContext, SkillError, SkillResult, serve
from sandman_agent.config import DEFAULT_CONTRACTS_DIR, Settings
from sandman_agent.contracts import Contracts
from sandman_agent.kg import KgClient
from sandman_agent.llm import ChatMessage, ChatRequest, OpenAiLlmClient
from sandman_agent.testing import InMemoryEventBus

URL = "http://tlr-agent:8080"
TOKENS = {"orchestrator": "orch-token"}
CARD = DEFAULT_CONTRACTS_DIR / "agent-cards" / "tlr-agent.json"
INPUT = {"projectId": "mini", "sourceVersionIds": ["ver_" + "0" * 32], "targetVersionIds": ["ver_" + "1" * 32]}
EVENT_DATA = {"projectId": "mini", "sourceVersionIds": [], "targetVersionIds": [], "runs": []}
RUN = {"approach": "stub", "approachVersion": "0", "runId": "run_" + "0" * 32, "status": "completed", "linkCount": 1}


class Recover:
    id = "recover-trace-links"

    def __init__(self, error: SkillError | None = None) -> None:
        self.error = error

    async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
        if self.error:
            raise self.error
        return SkillResult({"runs": [RUN]}, "sandman.tracelinks.updated", {**EVENT_DATA, "runs": [RUN]})


class FakeMsg:
    def __init__(self, data: bytes) -> None:
        self.data, self.metadata = data, SimpleNamespace(num_delivered=1)

    async def ack(self) -> None:
        return None

    async def nak(self, delay: float | None = None) -> None:
        return None


async def wait(client: A2aClient, task_id: str) -> Any:
    for _ in range(100):
        snap = await client.get(URL, task_id)
        if snap.terminal:
            return snap
        await asyncio.sleep(0.01)
    raise AssertionError("task did not finish")


SPANS = InMemorySpanExporter()
READER = InMemoryMetricReader()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(SPANS))
trace.set_tracer_provider(_provider)
metrics.set_meter_provider(MeterProvider(metric_readers=[READER]))


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    SPANS.clear()
    HTTPXClientInstrumentor().instrument()
    yield
    HTTPXClientInstrumentor().uninstrument()


def by_name(prefix: str) -> list[Any]:
    return [s for s in SPANS.get_finished_spans() if s.name.startswith(prefix)]


def metric_points(name: str) -> list[Any]:
    data = READER.get_metrics_data()
    points: list[Any] = []
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    points.extend(m.data.data_points)
    return points


def test_disabled_without_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    assert not telemetry.enabled()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    assert telemetry.enabled()
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert not telemetry.enabled()


async def test_a2a_client_to_server_shares_trace(settings: Settings, contracts: Contracts) -> None:
    bus = InMemoryEventBus(contracts, "tlr-agent")
    app = serve(CARD, [Recover()], settings, events=bus, contracts=contracts)
    client = A2aClient(TOKENS["orchestrator"], transport=httpx.ASGITransport(app=app))
    with telemetry.tracer().start_as_current_span("root"):
        task = await client.submit(URL, "recover-trace-links", INPUT)
        await wait(client, task.task_id)
    root = by_name("root")[0]
    server = by_name("a2a.task")[0]
    assert server.context.trace_id == root.context.trace_id
    assert server.attributes["sandman.skill"] == "recover-trace-links"
    assert server.attributes["sandman.agent"] == "tlr-agent"
    assert server.attributes["sandman.outcome"] == "completed"
    assert server.attributes["sandman.task.id"] == task.task_id
    assert by_name("a2a.client send")[0].context.trace_id == root.context.trace_id
    assert "completed" in {p.attributes["sandman.outcome"] for p in metric_points("sandman.a2a.task.duration")}


async def test_failed_task_is_marked(settings: Settings, contracts: Contracts) -> None:
    app = serve(CARD, [Recover(SkillError("X", "no", False))], settings, contracts=contracts)
    client = A2aClient(TOKENS["orchestrator"], transport=httpx.ASGITransport(app=app))
    await wait(client, (await client.submit(URL, "recover-trace-links", INPUT)).task_id)
    span = by_name("a2a.task")[0]
    assert span.attributes["sandman.outcome"] == "failed" and not span.status.is_ok


class Js:
    def __init__(self) -> None:
        self.payload = b""

    async def publish(self, subject: str, payload: bytes, headers: dict[str, str]) -> None:
        self.payload = payload


async def test_event_publish_to_consume_shares_trace(contracts: Contracts) -> None:
    js: Any = Js()
    with telemetry.tracer().start_as_current_span("root"):
        event = await events.EventPublisher(js, contracts, "tlr-agent").publish(
            "sandman.tracelinks.updated", "mini", EVENT_DATA, causation_id="m-1"
        )
    assert event.traceparent is not None
    contracts.validate("events.schema.json", json.loads(js.payload))

    async def handler(e: events.Event) -> None:
        return None

    msg = FakeMsg(js.payload)
    await events.handle_message(msg, handler)
    publish, consume = by_name("event.publish")[0], by_name("event.consume")[0]
    assert publish.context.trace_id == consume.context.trace_id == by_name("root")[0].context.trace_id
    assert consume.parent is not None and consume.parent.span_id == publish.context.span_id
    assert consume.attributes["sandman.event.type"] == "sandman.tracelinks.updated"
    published = {p.attributes["sandman.event.type"] for p in metric_points("sandman.events.published")}
    assert "sandman.tracelinks.updated" in published
    assert "ok" in {p.attributes["sandman.outcome"] for p in metric_points("sandman.events.consumed")}


class FakeTx:
    async def run(self, cypher: str, params: dict[str, Any]) -> Any:
        async def rows() -> Any:
            yield SimpleNamespace(data=lambda: {"ok": 1})

        return rows()


class FakeSession:
    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute_read(self, fn: Any) -> Any:
        return await fn(FakeTx())

    execute_write = execute_read


async def test_kg_span_has_statement_but_no_parameters() -> None:
    kg = KgClient("bolt://x", None, None, driver=SimpleNamespace(session=FakeSession))  # type: ignore[arg-type]
    await kg.read("MATCH (n {id: $id}) RETURN n", id="SECRET-VALUE")
    await kg.write("CREATE (n {id: $id})", id="SECRET-VALUE")
    await kg.write_tx(lambda tx: _noop())
    spans = by_name("kg ")
    assert [s.name for s in spans] == ["kg read", "kg write", "kg write_tx"]
    assert spans[0].attributes["db.system.name"] == "memgraph"
    assert spans[0].attributes["db.query.text"] == "MATCH (n {id: $id}) RETURN n"
    assert "SECRET-VALUE" not in json.dumps([dict(s.attributes or {}) for s in spans])


async def _noop() -> int:
    return 1


async def test_llm_span_and_token_metrics() -> None:
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7)
    reply = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="PROMPT-ANSWER"), finish_reason="stop")],
        usage=usage,
        model="gpt-resolved",
    )

    async def create(**kwargs: Any) -> Any:
        assert "traceparent" in kwargs["extra_headers"]
        return reply

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client = OpenAiLlmClient("http://x", "k", {"chat-default": "openai/gpt-x"}, _client=sdk)  # type: ignore[arg-type]
    await client.chat(ChatRequest("chat-default", [ChatMessage("user", "PROMPT-SECRET")]))
    span = by_name("chat")[0]
    assert span.attributes["gen_ai.request.model"] == "openai/gpt-x"
    assert span.attributes["gen_ai.response.model"] == "gpt-resolved"
    assert span.attributes["gen_ai.usage.input_tokens"] == 11 and span.attributes["gen_ai.usage.output_tokens"] == 7
    assert "PROMPT" not in json.dumps(dict(span.attributes or {}))
    (inp,) = metric_points("gen_ai.client.inference.usage.input_tokens")
    assert inp.value >= 11 and inp.attributes["sandman.llm.route"] == "chat-default"
    assert metric_points("gen_ai.client.inference.usage.output_tokens")[0].value >= 7


def test_continued_span_joins_stored_trace() -> None:
    with telemetry.tracer().start_as_current_span("origin"):
        stored = telemetry.trace_headers()["traceparent"]
    with telemetry.continued_span("later", stored):
        pass
    assert by_name("later")[0].context.trace_id == by_name("origin")[0].context.trace_id


def test_exception_messages_and_urls_stay_out_of_spans() -> None:
    with pytest.raises(ValueError):
        with telemetry.client_span("failing"):
            raise ValueError("SECRET-MESSAGE")
    span = by_name("failing")[0]
    assert span.status.status_code.name == "ERROR" and span.status.description == "ValueError"
    assert not span.events and "SECRET" not in json.dumps(dict(span.attributes or {}))

    with telemetry.tracer().start_as_current_span("http", attributes={"url.full": "http://x/a?token=SECRET#f"}) as http:
        telemetry.strip_query(http, httpx.Request("GET", "http://x/a?token=SECRET#f"))
    assert by_name("http")[0].attributes["url.full"] == "http://x/a"


async def test_event_handler_error_message_not_exported() -> None:
    event = events.build_event(
        Contracts(DEFAULT_CONTRACTS_DIR), "x", "sandman.tracelinks.updated", "mini", EVENT_DATA, None, "m-1", None
    )

    async def boom(e: events.Event) -> None:
        raise RuntimeError("SECRET-HANDLER")

    await events.handle_message(FakeMsg(json.dumps(event.to_json()).encode()), boom)
    span = by_name("event.consume")[0]
    assert span.status.description == "RuntimeError" and not span.events


def test_kg_statement_is_truncated() -> None:
    with telemetry.kg_span("read", "X" * 5000):
        pass
    assert len(by_name("kg read")[0].attributes["db.query.text"]) == telemetry.MAX_STATEMENT_CHARS
