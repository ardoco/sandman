"""History of the dashboard: a sampler writes the poller cache into SQLite, queries downsample it for the charts."""

import asyncio
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any

from sandman_dashboard.alerts import parse_size

log = logging.getLogger(__name__)

HEALTH = {"down": 0.0, "degraded": 1.0, "up": 2.0}
GAUGES = ("health", "active_tasks", "memgraph_memory")
COUNTERS = ("llm_requests", "llm_tokens_in", "llm_tokens_out", "llm_cost", "llm_errors")
GATEWAY_FIELDS = {
    "llm_requests": "requests",
    "llm_tokens_in": "input_tokens",
    "llm_tokens_out": "output_tokens",
    "llm_cost": "cost",
    "llm_errors": "errors",
}
RANGES = {"1h": 3600, "24h": 86400, "14d": 14 * 86400}
MAX_POINTS = 1000
PRUNE_EVERY = 3600.0
LOCK = threading.Lock()


def collect(poller: Any) -> list[tuple[str, str, float]]:
    """(series, key, value) of the current poller cache; missing entries simply produce no rows."""
    rows: list[tuple[str, str, float]] = []
    for name, entry in list(poller.entries.items()):
        rows.append(("health", name, HEALTH.get(entry.health, 0.0)))
        if entry.active_tasks is not None:
            rows.append(("active_tasks", name, float(entry.active_tasks)))
    memgraph = poller.entries.get("memgraph")
    memory = parse_size(memgraph.details.get("memory")) if memgraph is not None and memgraph.health == "up" else None
    if memory is not None:
        rows.append(("memgraph_memory", "memgraph", memory))
    snapshot = poller.gateway_snapshot
    if snapshot is not None:
        totals: dict[tuple[str, str], float] = {}
        for row in snapshot.rows:
            for series, attr in GATEWAY_FIELDS.items():
                value = getattr(row, attr)
                if value is not None:
                    totals[(series, row.key)] = totals.get((series, row.key), 0.0) + value
        rows += [(series, key, value) for (series, key), value in sorted(totals.items())]
    return rows


class Sampler:
    def __init__(
        self,
        db: sqlite3.Connection,
        poller: Any,
        *,
        interval: float = 30.0,
        retention_days: float = 14.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db, self.poller, self.interval, self.retention = db, poller, interval, retention_days * 86400
        self.clock = clock
        self._task: asyncio.Task[None] | None = None
        self._pruned = float("-inf")

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.sample_once)
            except Exception:  # noqa: BLE001 - the sampler must survive any error
                log.exception("sampling failed")
            await asyncio.sleep(self.interval)

    def sample_once(self) -> int:
        """Writes one sample set (and prunes hourly); returns the number of rows written."""
        with LOCK:
            return self._sample()

    def _sample(self) -> int:
        now = int(self.clock())
        rows = [(now, series, key, value) for series, key, value in collect(self.poller)]
        self.db.executemany("INSERT INTO samples VALUES (?, ?, ?, ?)", rows)
        if now - self._pruned >= PRUNE_EVERY:
            self.prune()
            self._pruned = now
        return len(rows)

    def prune(self) -> int:
        cursor = self.db.execute("DELETE FROM samples WHERE ts < ?", (int(self.clock() - self.retention),))
        return cursor.rowcount


Series = dict[str, list[list[float]]]


def gauge_series(db: sqlite3.Connection, series: str, start: int, end: int) -> Series:
    """Per key at most MAX_POINTS points; buckets keep their minimum and maximum so short dips survive."""
    width = max(1, -(-(end - start) // (MAX_POINTS // 2 - 1)))
    out: Series = {}
    rows = db.execute(
        "SELECT key, (ts - ?1) / ?2 AS b, min(value), max(value), min(ts), max(ts), count(*) FROM samples "
        "WHERE series = ?3 AND ts >= ?1 AND ts <= ?4 GROUP BY key, b ORDER BY key, b",
        (start, width, series, end),
    )
    for key, _, low, high, first, last, count in rows:
        points = out.setdefault(key, [])
        if count == 1 or low == high:
            points.append([first, high])
            if count > 1:
                points.append([last, high])
        else:
            points += [[first, low], [last, high]] if first <= last else [[first, high], [last, low]]
    return out


def counter_series(db: sqlite3.Connection, series: str, start: int, end: int) -> Series:
    """Increases of a cumulative counter per bucket; a decrease is a reset and the new value counts."""
    width = max(1, -(-(end - start) // (MAX_POINTS - 1)))
    out: Series = {}
    rows = db.execute(
        "WITH d AS (SELECT key, ts, CASE WHEN prev IS NULL THEN NULL WHEN value >= prev THEN value - prev "
        "ELSE value END AS delta FROM (SELECT key, ts, value, LAG(value) OVER (PARTITION BY key ORDER BY ts) "
        "AS prev FROM samples WHERE series = ?3 AND ts >= ?1 AND ts <= ?4)) "
        "SELECT key, (ts - ?1) / ?2 AS b, sum(delta), max(ts) FROM d WHERE delta IS NOT NULL "
        "GROUP BY key, b ORDER BY key, b",
        (start, width, series, end),
    )
    for key, _, total, last in rows:
        out.setdefault(key, []).append([last, total])
    return out


def history(db: sqlite3.Connection, range_: str, now: float | None = None) -> dict[str, Any]:
    with LOCK:
        return _history(db, range_, now)


def _history(db: sqlite3.Connection, range_: str, now: float | None) -> dict[str, Any]:
    seconds = RANGES[range_]
    end = int(time.time() if now is None else now)
    start = end - seconds
    data = {s: gauge_series(db, s, start, end) for s in GAUGES}
    data |= {s: counter_series(db, s, start, end) for s in COUNTERS}
    return {"range": range_, "from": start, "to": end, "series": data}
