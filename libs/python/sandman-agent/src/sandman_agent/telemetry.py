"""JSON logging with context fields, optional file sink, optional OpenTelemetry tracing."""

import contextvars
import json
import logging
import logging.handlers
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    """Configure the root logger: stdout plus optional rotating JSONL file."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(log_file, maxBytes=50_000_000, backupCount=5))
    for h in handlers:
        h.setFormatter(JsonFormatter(service))
    logging.basicConfig(level=level, handlers=handlers, force=True)
    if os.environ.get("OTEL_SDK_DISABLED", "true").lower() == "false":
        _setup_tracing(service)


def _setup_tracing(service: str) -> None:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


def _trace_ids() -> dict[str, str]:
    from opentelemetry import trace

    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {"traceId": format(ctx.trace_id, "032x"), "spanId": format(ctx.span_id, "016x")}


def trace_headers() -> dict[str, str]:
    """W3C traceparent of the current span (empty without an active span)."""
    from opentelemetry.propagate import inject

    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier
