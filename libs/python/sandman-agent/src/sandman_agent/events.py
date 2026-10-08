"""Domain events on NATS JetStream with the CloudEvents envelope (ADR-0003)."""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, DiscardPolicy, RetentionPolicy, StorageType, StreamConfig

from sandman_agent import telemetry
from sandman_agent.contracts import Contracts

log = logging.getLogger(__name__)

STREAM = "SANDMAN"
STREAM_CONFIG = StreamConfig(
    name=STREAM,
    subjects=["sandman.>"],
    storage=StorageType.FILE,
    retention=RetentionPolicy.LIMITS,
    max_age=90 * 24 * 3600,
    duplicate_window=120,
    discard=DiscardPolicy.OLD,
    num_replicas=1,
)
CONSUMER_BACKOFF = (5.0, 30.0, 120.0, 300.0)


class PublishError(Exception):
    """Event could not be published after retries."""


@dataclass(frozen=True)
class Event:
    """CloudEvents 1.0 envelope; subject = projectId."""

    id: str
    type: str
    source: str
    subject: str
    time: str
    data: Mapping[str, Any]
    correlation_id: str | None = None
    causation_id: str | None = None
    traceparent: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Envelope as JSON object without null members."""
        envelope: dict[str, Any] = {
            "specversion": "1.0",
            "id": self.id,
            "source": self.source,
            "type": self.type,
            "subject": self.subject,
            "time": self.time,
            "datacontenttype": "application/json",
            "data": dict(self.data),
        }
        for key, value in (
            ("correlationid", self.correlation_id),
            ("causationid", self.causation_id),
            ("traceparent", self.traceparent),
        ):
            if value is not None:
                envelope[key] = value
        return envelope

    @classmethod
    def from_json(cls, envelope: Mapping[str, Any]) -> "Event":
        """Parse an envelope."""
        return cls(
            envelope["id"], envelope["type"], envelope["source"], envelope["subject"], envelope["time"],
            envelope["data"], envelope.get("correlationid"), envelope.get("causationid"), envelope.get("traceparent"),
        )  # fmt: skip


class EventSink(Protocol):
    """Anything that publishes events (EventPublisher, InMemoryEventBus)."""

    async def publish(
        self,
        type: str,
        subject: str,
        data: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        event_id: str | None = None,
    ) -> Event:
        """Validate and publish one event."""
        ...


def build_event(
    contracts: Contracts,
    source: str,
    type: str,
    subject: str,
    data: Mapping[str, Any],
    correlation_id: str | None,
    causation_id: str | None,
    event_id: str | None,
) -> Event:
    """Create and validate an event (contracts/events.schema.json)."""
    event = Event(
        id=event_id or str(uuid.uuid4()),
        type=type,
        source=f"sandman/{source}",
        subject=subject,
        time=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        data=data,
        correlation_id=correlation_id,
        causation_id=causation_id,
        traceparent=telemetry.trace_headers().get("traceparent"),
    )
    contracts.validate("events.schema.json", event.to_json())
    return event


class EventPublisher:
    """Publishes to JetStream: Nats-Msg-Id = event id, waits for the ack, retries 3x, then raises."""

    def __init__(
        self, js: JetStreamContext, contracts: Contracts, source: str, backoff: Sequence[float] = (0.5, 2.0, 5.0)
    ) -> None:
        self._js, self._contracts, self._source, self._backoff = js, contracts, source, backoff

    async def publish(
        self,
        type: str,
        subject: str,
        data: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        event_id: str | None = None,
    ) -> Event:
        """Validate, publish and await the JetStream ack."""
        with telemetry.event_publish_span(type):
            event = build_event(
                self._contracts, self._source, type, subject, data, correlation_id, causation_id, event_id
            )
            payload = json.dumps(event.to_json(), ensure_ascii=False).encode()
            for attempt, delay in enumerate((*self._backoff, None)):
                try:
                    await self._js.publish(type, payload, headers={"Nats-Msg-Id": event.id})
                    telemetry.event_published(type)
                    return event
                except Exception as e:  # noqa: BLE001 - any transport error is retried, then surfaced
                    if delay is None:
                        raise PublishError(f"publishing {type} {event.id} failed after {attempt + 1} attempts") from e
                    log.warning("publish failed, retrying", extra={"fields": {"event": type, "attempt": attempt + 1}})
                    await asyncio.sleep(delay)
            raise AssertionError("unreachable")


async def ensure_stream(js: JetStreamContext) -> None:
    """Create or update the SANDMAN stream (idempotent)."""
    try:
        await js.stream_info(STREAM)
        await js.update_stream(STREAM_CONFIG)
    except Exception:  # noqa: BLE001 - stream does not exist yet
        await js.add_stream(STREAM_CONFIG)


Handler = Callable[[Event], Awaitable[None]]


class EventConsumer:
    """Durable pull consumer: ack on success, nak with backoff delay on handler exceptions."""

    def __init__(self, js: JetStreamContext, durable: str, subject: str = "sandman.>") -> None:
        self._js, self._durable, self._subject = js, durable, subject

    def config(self) -> ConsumerConfig:
        """Consumer configuration of Stage 2 §1.4."""
        return ConsumerConfig(
            durable_name=self._durable,
            filter_subject=self._subject,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=60,
            max_deliver=5,
            backoff=list(CONSUMER_BACKOFF),
            max_ack_pending=100,
        )

    async def run(self, handler: Handler, stop: asyncio.Event | None = None) -> None:
        """Fetch and handle messages until stop is set."""
        sub = await self._js.pull_subscribe(self._subject, durable=self._durable, config=self.config())
        while stop is None or not stop.is_set():
            try:
                msgs = await sub.fetch(batch=10, timeout=2)
            except TimeoutError:
                continue
            for msg in msgs:
                await handle_message(msg, handler)


async def handle_message(msg: Any, handler: Handler) -> None:
    """Ack after the handler succeeded; nak with the next backoff delay otherwise."""
    try:
        event = Event.from_json(json.loads(msg.data))
        with telemetry.event_consume_span(event.type, event.id, event.traceparent):
            await handler(event)
    except Exception:
        delivered = getattr(getattr(msg, "metadata", None), "num_delivered", 1) or 1
        delay = CONSUMER_BACKOFF[min(delivered, len(CONSUMER_BACKOFF)) - 1]
        log.exception("event handler failed", extra={"fields": {"delivered": delivered}})
        await msg.nak(delay=delay)
        return
    await msg.ack()
