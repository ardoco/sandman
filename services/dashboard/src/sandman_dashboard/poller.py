"""Background poller: health and activity of the SANDMAN services and infrastructure, kept in memory.

Pages read only this cache. Upstream error bodies and request headers are never stored.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from sandman_agent.kg import KgClient, now

from sandman_dashboard.gateway import GatewayClient, GatewaySnapshot

log = logging.getLogger(__name__)

TIMEOUT = 2.0
COUNT_INTERVAL = 60.0
STATES = ("working", "idle")
MAX_DETAILS = 30
MAX_TEXT = 300
STORAGE_KEYS = {"memory_res": "memory", "memory_limit": "memoryLimit", "disk_usage": "disk"}


@dataclass
class Entry:
    name: str
    kind: str  # service | infra
    health: str = "down"  # up | degraded | down
    checked_at: str | None = None
    last_ok_at: str | None = None
    reason: str = ""
    state: str | None = None
    active_tasks: int | None = None
    last_activity_at: str | None = None
    version: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def parse_targets(spec: str) -> dict[str, str]:
    """`name=url,name=url` -> {name: url}."""
    targets: dict[str, str] = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        name, sep, url = part.partition("=")
        if not sep or not name.strip() or not url.strip():
            raise ValueError(f"invalid SANDMAN_TARGETS entry {part!r}")
        targets[name.strip()] = url.strip().rstrip("/")
    return targets


def clean(value: Any) -> Any:
    """Bounded JSON-like value for display."""
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k)[:MAX_TEXT]: clean(v) for k, v in list(value.items())[:MAX_DETAILS]}
    if isinstance(value, list | tuple):
        return [clean(v) for v in value[:MAX_DETAILS]]
    return str(value)[:MAX_TEXT]


def valid_status(doc: Any) -> bool:
    return (
        isinstance(doc, dict)
        and doc.get("state") in STATES
        and isinstance(doc.get("activeTasks"), int)
        and not isinstance(doc["activeTasks"], bool)
        and doc["activeTasks"] >= 0
    )


class Poller:
    def __init__(
        self,
        targets: Mapping[str, str],
        client: httpx.AsyncClient,
        token: str,
        *,
        interval: float = 5.0,
        kg: KgClient | None = None,
        nats_monitor_url: str | None = None,
        gateway: GatewayClient | None = None,
    ) -> None:
        self.targets, self.client, self.token, self.interval = dict(targets), client, token, interval
        self.kg, self.nats_url, self.gateway = kg, (nats_monitor_url or "").rstrip("/"), gateway
        self.entries: dict[str, Entry] = {}
        self.gateway_snapshot: GatewaySnapshot | None = None
        self._counts: dict[str, Any] | None = None
        self._counts_at = 0.0
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 - the loop must survive any poll error
                log.exception("poll failed")
            await asyncio.sleep(self.interval)

    async def poll_once(self) -> None:
        jobs: list[tuple[str, str, Callable[[Entry], Awaitable[None]]]] = [
            (n, "service", self._service(u)) for n, u in self.targets.items()
        ]
        if self.kg:
            jobs.append(("memgraph", "infra", self._memgraph))
        if self.nats_url:
            jobs.append(("nats", "infra", self._nats))
        if self.gateway:
            jobs.append(("llm-gateway", "infra", self._gateway))
        results = await asyncio.gather(*(self._run(n, k, fn) for n, k, fn in jobs))
        self.entries = {e.name: e for e in results}
        if self.gateway and self.entries["llm-gateway"].health != "up":
            self.gateway_snapshot = None

    async def _run(self, name: str, kind: str, check: Callable[[Entry], Awaitable[None]]) -> Entry:
        previous = self.entries.get(name)
        entry = Entry(name, kind, last_ok_at=previous.last_ok_at if previous else None)
        try:
            async with asyncio.timeout(TIMEOUT * 2):
                await check(entry)
        except TimeoutError:
            entry.health, entry.reason = "down", "timeout"
        except httpx.HTTPError:
            entry.health, entry.reason = "down", "connection error"
        except Exception as e:  # noqa: BLE001 - any failure of a check means down; the message is not shown
            log.warning("check failed", extra={"fields": {"target": name, "error": type(e).__name__}})
            entry.health, entry.reason = "down", "check failed"
        entry.checked_at = now()
        if entry.health == "up":
            entry.last_ok_at = entry.checked_at
        return entry

    async def _get(self, url: str, headers: Mapping[str, str] | None = None) -> httpx.Response:
        async with asyncio.timeout(TIMEOUT):
            return await self.client.get(url, headers=headers, timeout=TIMEOUT)

    def _service(self, base: str) -> Callable[[Entry], Awaitable[None]]:
        async def check(entry: Entry) -> None:
            ready, status = await asyncio.gather(
                self._get(f"{base}/readyz"),
                self._get(f"{base}/v1/status", {"Authorization": f"Bearer {self.token}"}),
                return_exceptions=True,
            )
            if isinstance(ready, BaseException) or ready.status_code != 200:
                entry.health = "down"
                entry.reason = "not ready" if isinstance(ready, httpx.Response) else _reason(ready)
                return
            doc: Any = None
            if isinstance(status, httpx.Response) and status.status_code == 200:
                try:
                    doc = status.json()
                except ValueError:
                    doc = None
            if not valid_status(doc):
                entry.health = "degraded"
                entry.reason = "status unavailable" if not isinstance(status, httpx.Response) else "status invalid"
                return
            entry.health = "up"
            entry.state, entry.active_tasks = doc["state"], doc["activeTasks"]
            entry.last_activity_at = (
                clean(doc.get("lastActivityAt")) if isinstance(doc.get("lastActivityAt"), str) else None
            )
            entry.version = clean(doc.get("version")) if isinstance(doc.get("version"), str) else None
            entry.details = clean(doc.get("details")) if isinstance(doc.get("details"), dict) else {}

        return check

    async def _memgraph(self, entry: Entry) -> None:
        assert self.kg
        async with asyncio.timeout(TIMEOUT):
            await self.kg.read("RETURN 1 AS ok")
            if self._counts is None or time.monotonic() - self._counts_at >= COUNT_INTERVAL:
                nodes = await self.kg.read("MATCH (n) RETURN count(n) AS c")
                edges = await self.kg.read("MATCH ()-[r]->() RETURN count(r) AS c")
                self._counts, self._counts_at = {"nodes": nodes[0]["c"], "edges": edges[0]["c"]}, time.monotonic()
            rows = await self.kg.run_autocommit("SHOW STORAGE INFO")
        info = {r.get("storage info"): r.get("value") for r in rows}
        entry.details = dict(self._counts)
        entry.details |= {label: clean(info[key]) for key, label in STORAGE_KEYS.items() if key in info}
        entry.health = "up"

    async def _nats(self, entry: Entry) -> None:
        health, jsz = await asyncio.gather(
            self._get(f"{self.nats_url}/healthz?js-enabled-only=true"),
            self._get(f"{self.nats_url}/jsz?streams=true&consumers=true"),
        )
        if health.status_code != 200:
            entry.health, entry.reason = "down", "not healthy"
            return
        entry.health = "up"
        if jsz.status_code == 200:
            entry.details = nats_details(jsz.json())

    async def _gateway(self, entry: Entry) -> None:
        assert self.gateway
        health, stats = await asyncio.gather(
            self._get(f"{self.gateway.url}/health"),
            self.gateway.fetch(self.gateway_snapshot),
            return_exceptions=True,
        )
        if isinstance(health, BaseException) or health.status_code != 200:
            entry.health = "down"
            entry.reason = _reason(health) if isinstance(health, BaseException) else "not healthy"
            return
        entry.health = "up"
        if isinstance(stats, GatewaySnapshot):
            self.gateway_snapshot = stats
        else:
            self.gateway_snapshot = None
            log.warning("gateway statistics unavailable")


def _reason(error: BaseException) -> str:
    return "timeout" if isinstance(error, TimeoutError | httpx.TimeoutException) else "connection error"


def nats_details(jsz: Any) -> dict[str, Any]:
    """Stream SANDMAN size and consumer lag from a /jsz?streams=true&consumers=true document."""
    details: dict[str, Any] = {}
    if not isinstance(jsz, dict):
        return details
    for account in jsz.get("account_details") or []:
        for stream in account.get("stream_detail") or []:
            if stream.get("name") != "SANDMAN":
                continue
            state = stream.get("state") or {}
            details["stream"] = {"messages": state.get("messages"), "bytes": state.get("bytes")}
            for consumer in stream.get("consumer_detail") or []:
                if consumer.get("name") == "orchestrator":
                    details["consumer"] = {
                        "pending": consumer.get("num_pending"),
                        "ackPending": consumer.get("num_ack_pending"),
                        "redelivered": consumer.get("num_redelivered"),
                    }
    return clean(details)  # type: ignore[no-any-return]
