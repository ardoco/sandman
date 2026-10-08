"""JSON logging with context fields, optional OpenTelemetry traces and metrics (no-op without an OTLP endpoint)."""

import contextvars
import json
import logging
import logging.handlers
import os
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

INSTRUMENTATION = "sandman_agent"
SEMCONV_GENAI = "open-telemetry/semantic-conventions-genai main (status Development), checked 2026-10-08"

SPAN_A2A_TASK = "a2a.task"
SPAN_A2A_CLIENT = "a2a.client"
SPAN_EVENT_PUBLISH = "event.publish"
SPAN_EVENT_CONSUME = "event.consume"
SPAN_KG = "kg"
SPAN_PLUGIN_EXTRACT = "plugin.extract"

ATTR_AGENT = "sandman.agent"
ATTR_SKILL = "sandman.skill"
ATTR_TASK_ID = "sandman.task.id"
ATTR_OUTCOME = "sandman.outcome"
ATTR_EVENT_TYPE = "sandman.event.type"
ATTR_EVENT_ID = "sandman.event.id"
ATTR_ROUTE = "sandman.llm.route"
ATTR_MESSAGING_SYSTEM = "messaging.system"
ATTR_DB_SYSTEM = "db.system.name"
ATTR_DB_STATEMENT = "db.query.text"
ATTR_DB_OPERATION = "db.operation.name"
ATTR_GENAI_OPERATION = "gen_ai.operation.name"
ATTR_GENAI_PROVIDER = "gen_ai.provider.name"
ATTR_GENAI_REQUEST_MODEL = "gen_ai.request.model"
ATTR_GENAI_RESPONSE_MODEL = "gen_ai.response.model"
ATTR_GENAI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_GENAI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ATTR_ERROR_TYPE = "error.type"

METRIC_A2A_TASK_DURATION = "sandman.a2a.task.duration"
METRIC_EVENTS_PUBLISHED = "sandman.events.published"
METRIC_EVENTS_CONSUMED = "sandman.events.consumed"
METRIC_EVENT_CONSUME_DURATION = "sandman.events.consume.duration"
METRIC_LLM_INPUT_TOKENS = "gen_ai.client.inference.usage.input_tokens"
METRIC_LLM_OUTPUT_TOKENS = "gen_ai.client.inference.usage.output_tokens"
METRIC_LLM_DURATION = "gen_ai.client.inference.duration"

_meter = metrics.get_meter(INSTRUMENTATION)
_a2a_duration = _meter.create_histogram(METRIC_A2A_TASK_DURATION, unit="s", description="A2A task duration")
_events_published = _meter.create_counter(METRIC_EVENTS_PUBLISHED, description="Events published")
_events_consumed = _meter.create_counter(METRIC_EVENTS_CONSUMED, description="Events consumed")
_consume_duration = _meter.create_histogram(METRIC_EVENT_CONSUME_DURATION, unit="s", description="Handler duration")
_llm_input = _meter.create_counter(METRIC_LLM_INPUT_TOKENS, unit="{token}", description="LLM input tokens")
_llm_output = _meter.create_counter(METRIC_LLM_OUTPUT_TOKENS, unit="{token}", description="LLM output tokens")
_llm_duration = _meter.create_histogram(METRIC_LLM_DURATION, unit="s", description="LLM call duration")

MAX_STATEMENT_CHARS = 2000
FASTAPI_TELEMETRY: dict[str, Any] = {"auto_configure": False, "operation_spans": False}

CONTEXT_FIELDS = ("workflowId", "taskId", "runId", "projectId", "actor")
_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("sandman_log_context", default={})  # noqa: B039


def bind(**fields: str | None) -> contextvars.Token[dict[str, str]]:
    """Add context fields (e.g. runId) to all log lines of the current task."""
    merged = {**_context.get(), **{k: v for k, v in fields.items() if v is not None}}
    return _context.set(merged)


def reset(token: contextvars.Token[dict[str, str]]) -> None:
    """Undo bind()."""
    _context.reset(token)


class JsonFormatter(logging.Formatter):
    """One JSON object per line (Stage 2 §1.7 fields)."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service,
            "msg": record.getMessage(),
        }
        line.update(_context.get())
        line.update(_trace_ids())
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            line.update(extra)
        if record.exc_info:
            line["error"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False, default=str)


def setup_logging(service: str, log_file: Path | None = None, level: int = logging.INFO) -> None:
    """Configure the root logger (stdout plus optional rotating JSONL file) and, if enabled, OpenTelemetry."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(log_file, maxBytes=50_000_000, backupCount=5))
    for h in handlers:
        h.setFormatter(JsonFormatter(service))
    logging.basicConfig(level=level, handlers=handlers, force=True)
    if enabled():
        _setup_otel(service)


def enabled() -> bool:
    """Telemetry runs only with OTEL_SDK_DISABLED=false and an OTLP endpoint."""
    if os.environ.get("OTEL_SDK_DISABLED", "true").lower() != "false":
        return False
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))


def _setup_otel(service: str) -> None:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15_000)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    HTTPXClientInstrumentor().instrument(request_hook=strip_query, async_request_hook=strip_query)


def strip_query(span: Span, request: Any) -> None:
    """httpx instrumentation hook: URLs without query string (it may carry tokens or search terms)."""
    url = str(request.url.copy_with(query=None, fragment=None))
    present = getattr(span, "attributes", None) or {}
    for key in ("url.full", "http.url"):
        if key in present:
            span.set_attribute(key, url)


def _trace_ids() -> dict[str, str]:
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {"traceId": format(ctx.trace_id, "032x"), "spanId": format(ctx.span_id, "016x")}


def trace_headers() -> dict[str, str]:
    """W3C traceparent of the current span (empty without an active span)."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def tracer() -> trace.Tracer:
    """The library tracer."""
    return trace.get_tracer(INSTRUMENTATION)


def context_from(carrier: Mapping[str, str]) -> Context:
    """Remote parent context from W3C headers (keys lower-case)."""
    return extract(dict(carrier))


def context_from_traceparent(traceparent: str | None) -> Context | None:
    """Parent context of an event envelope's traceparent."""
    return extract({"traceparent": traceparent}) if traceparent else None


@contextmanager
def _span(
    name: str, *, kind: SpanKind = SpanKind.INTERNAL, attributes: Mapping[str, Any] | None = None,
    context: Context | None = None,
) -> Iterator[Span]:  # fmt: skip
    """Current span without exception messages: failures set ERROR with the exception type name only."""
    with tracer().start_as_current_span(
        name, context=context, kind=kind, attributes=attributes, record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield span
        except BaseException as e:
            span.set_status(Status(StatusCode.ERROR, type(e).__name__))
            raise


@contextmanager
def a2a_task_span(agent: str, skill: str, task_id: str, headers: Mapping[str, str]) -> Iterator[dict[str, str]]:
    """Server span of one A2A task, child of the caller's traceparent; the body sets result['outcome']."""
    result = {"outcome": "completed"}
    start = time.monotonic()
    attrs = {ATTR_AGENT: agent, ATTR_SKILL: skill, ATTR_TASK_ID: task_id}
    with _span(
        f"{SPAN_A2A_TASK} {skill}", context=context_from(headers), kind=SpanKind.SERVER, attributes=attrs
    ) as span:
        try:
            yield result
        finally:
            span.set_attribute(ATTR_OUTCOME, result["outcome"])
            if result["outcome"] != "completed":
                span.set_status(Status(StatusCode.ERROR, result["outcome"]))
            _a2a_duration.record(
                time.monotonic() - start, {ATTR_AGENT: agent, ATTR_SKILL: skill, ATTR_OUTCOME: result["outcome"]}
            )


def continued_span(name: str, traceparent: str | None) -> Any:
    """Internal span continuing a stored traceparent (a new trace without one)."""
    return _span(name, context=context_from_traceparent(traceparent))


def client_span(name: str, **attributes: str) -> Any:
    """Client span context manager."""
    return _span(name, kind=SpanKind.CLIENT, attributes=attributes)


def kg_span(operation: str, statement: str | None = None) -> Any:
    """Memgraph span; the statement text only, never parameters."""
    attrs = {ATTR_DB_SYSTEM: "memgraph", ATTR_DB_OPERATION: operation}
    if statement is not None:
        attrs[ATTR_DB_STATEMENT] = statement[:MAX_STATEMENT_CHARS]
    return _span(f"{SPAN_KG} {operation}", kind=SpanKind.CLIENT, attributes=attrs)


def event_publish_span(event_type: str) -> Any:
    """Producer span around building and publishing one event."""
    attrs = {ATTR_MESSAGING_SYSTEM: "nats", ATTR_EVENT_TYPE: event_type}
    return _span(f"{SPAN_EVENT_PUBLISH} {event_type}", kind=SpanKind.PRODUCER, attributes=attrs)


def event_published(event_type: str) -> None:
    """Count one published event."""
    _events_published.add(1, {ATTR_EVENT_TYPE: event_type})


@contextmanager
def event_consume_span(event_type: str, event_id: str, traceparent: str | None) -> Iterator[Span]:
    """Consumer span continuing the producer's trace; counts and times the handler."""
    start = time.monotonic()
    attrs = {ATTR_MESSAGING_SYSTEM: "nats", ATTR_EVENT_TYPE: event_type, ATTR_EVENT_ID: event_id}
    outcome = "ok"
    with _span(
        f"{SPAN_EVENT_CONSUME} {event_type}",
        context=context_from_traceparent(traceparent),
        kind=SpanKind.CONSUMER,
        attributes=attrs,
    ) as span:
        try:
            yield span
        except BaseException:
            outcome = "error"
            raise
        finally:
            metric_attrs = {ATTR_EVENT_TYPE: event_type, ATTR_OUTCOME: outcome}
            _events_consumed.add(1, metric_attrs)
            _consume_duration.record(time.monotonic() - start, metric_attrs)


@contextmanager
def llm_span(operation: str, route: str, request_model: str) -> Iterator[dict[str, Any]]:
    """GenAI client span; the body stores response_model, input_tokens and output_tokens in the yielded dict."""
    start = time.monotonic()
    out: dict[str, Any] = {}
    attrs = {ATTR_GENAI_OPERATION: operation, ATTR_GENAI_PROVIDER: "openai", ATTR_GENAI_REQUEST_MODEL: request_model}
    error: BaseException | None = None
    with _span(f"{operation} {request_model}", kind=SpanKind.CLIENT, attributes=attrs) as span:
        try:
            yield out
        except BaseException as e:
            error = e
            raise
        finally:
            metric_attrs: dict[str, Any] = {**attrs, ATTR_ROUTE: route}
            if error is not None:
                metric_attrs[ATTR_ERROR_TYPE] = type(error).__name__
            if "response_model" in out:
                span.set_attribute(ATTR_GENAI_RESPONSE_MODEL, out["response_model"])
                metric_attrs[ATTR_GENAI_RESPONSE_MODEL] = out["response_model"]
            for key, attr, counter in (
                ("input_tokens", ATTR_GENAI_INPUT_TOKENS, _llm_input),
                ("output_tokens", ATTR_GENAI_OUTPUT_TOKENS, _llm_output),
            ):
                if key in out:
                    span.set_attribute(attr, out[key])
                    counter.add(out[key], metric_attrs)
            _llm_duration.record(time.monotonic() - start, metric_attrs)


async def inject_request(request: Any) -> None:
    """httpx request hook: W3C headers of the current span (also for custom transports)."""
    inject(request.headers)
