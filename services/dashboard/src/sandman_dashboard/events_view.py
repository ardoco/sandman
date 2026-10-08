"""Read-only event stream viewer: one ephemeral ordered NATS consumer on `sandman.>` feeding an in-memory ring buffer.

The consumer has no acknowledgements and no durable name, so it never changes the delivery state of other consumers.
"""

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

import nats
from nats.js.api import ConsumerConfig, DeliverPolicy

log = logging.getLogger(__name__)

STREAM = "SANDMAN"
SUBJECT = "sandman.>"
RING = 500
MAX_PAYLOAD = 64 * 1024
RETRY = 5.0


@dataclass(frozen=True)
class Record:
    seq: int
    id: str
    type: str
    subject: str
    time: str
    source: str
    correlation_id: str
    payload: str


def _text(value: Any) -> str:
    return value[:200] if isinstance(value, str) else ""


def record(seq: int, raw: bytes) -> Record:
    """Ring entry from a stream message; malformed envelopes are kept as `(invalid)` with the truncated raw text."""
    try:
        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            raise ValueError("not an object")
        payload = json.dumps(envelope.get("data"), ensure_ascii=False, indent=2, sort_keys=True, default=str)
        fields = {k: _text(envelope.get(k)) for k in ("id", "type", "subject", "time", "source", "correlationid")}
    except ValueError:
        payload = raw.decode("utf-8", "replace")
        fields = {"id": "", "type": "(invalid)", "subject": "", "time": "", "source": "", "correlationid": ""}
    if len(payload) > MAX_PAYLOAD:
        payload = payload[:MAX_PAYLOAD] + "\n... truncated"
    return Record(
        seq,
        fields["id"],
        fields["type"],
        fields["subject"],
        fields["time"],
        fields["source"],
        fields["correlationid"],
        payload,
    )


class EventViewer:
    def __init__(self, url: str, size: int = RING) -> None:
        self.url, self.size = url, size
        self.buffer: deque[Record] = deque(maxlen=size)
        self._nc: Any = None
        self._task: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        return bool(self._nc and self._nc.is_connected)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def add(self, seq: int, raw: bytes) -> None:
        self.buffer.append(record(seq, raw))

    def listing(self, type_: str | None = None, project: str | None = None) -> list[Record]:
        """Newest first, optionally filtered by exact type and project (envelope subject)."""
        return [
            r for r in reversed(self.buffer) if (not type_ or r.type == type_) and (not project or r.subject == project)
        ]

    def find(self, seq: int) -> Record | None:
        return next((r for r in self.buffer if r.seq == seq), None)

    async def _loop(self) -> None:
        while True:
            try:
                await self._run()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - retried below, the viewer is best effort
                log.warning("event viewer failed", extra={"fields": {"error": type(e).__name__}})
            self._nc = None
            await asyncio.sleep(RETRY)

    async def _run(self) -> None:
        nc = await nats.connect(self.url, max_reconnect_attempts=-1, name="sandman-dashboard-viewer")
        self._nc = nc
        try:
            js = nc.jetstream()
            last = (await js.stream_info(STREAM)).state.last_seq
            self.buffer.clear()

            async def on_message(msg: Any) -> None:
                self.add(msg.metadata.sequence.stream, msg.data)

            config = ConsumerConfig(
                deliver_policy=DeliverPolicy.BY_START_SEQUENCE, opt_start_seq=max(1, last - self.size + 1)
            )
            await js.subscribe(SUBJECT, stream=STREAM, cb=on_message, ordered_consumer=True, config=config)
            while not nc.is_closed:
                await asyncio.sleep(1)
        finally:
            await nc.close()
