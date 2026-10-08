import sqlite3
from types import SimpleNamespace
from typing import Any

from sandman_dashboard.gateway import GatewaySnapshot, Row
from sandman_dashboard.poller import Entry
from sandman_dashboard.sampler import Sampler
from sandman_dashboard.store import SessionStore

DAY = 86400


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def rows(db: sqlite3.Connection, series: str | None = None) -> list[tuple[Any, ...]]:
    sql = "SELECT ts, series, key, value FROM samples" + (" WHERE series = ?" if series else "") + " ORDER BY ts, key"
    return [tuple(r) for r in db.execute(sql, (series,) if series else ()).fetchall()]


def poller(**entries: Entry) -> Any:
    return SimpleNamespace(entries=entries, gateway_snapshot=None)


def make(clock: Clock, p: Any) -> tuple[Sampler, sqlite3.Connection]:
    db = SessionStore(":memory:").db
    return Sampler(db, p, interval=30, retention_days=14, clock=clock), db


def test_one_row_set_per_interval_with_health_and_tasks() -> None:
    clock = Clock()
    p = poller(
        tlr=Entry("tlr", "service", "up", active_tasks=3),
        nats=Entry("nats", "infra", "degraded"),
        memgraph=Entry("memgraph", "infra", "up", details={"memory": "2.00MiB"}),
    )
    sampler, db = make(clock, p)
    assert sampler.sample_once() == 5
    clock.t += 30
    assert sampler.sample_once() == 5
    assert rows(db, "health")[:3] == [
        (1_000_000, "health", "memgraph", 2.0),
        (1_000_000, "health", "nats", 1.0),
        (1_000_000, "health", "tlr", 2.0),
    ]
    assert [r[3] for r in rows(db, "active_tasks")] == [3.0, 3.0]
    assert [r[3] for r in rows(db, "memgraph_memory")] == [2 * 1024**2] * 2


def test_target_going_down_is_zero_in_the_next_sample() -> None:
    clock = Clock()
    entry = Entry("tlr", "service", "up", active_tasks=0)
    sampler, db = make(clock, poller(tlr=entry))
    sampler.sample_once()
    clock.t += 30
    entry.health, entry.active_tasks = "down", None
    sampler.sample_once()
    assert [r[3] for r in rows(db, "health")] == [2.0, 0.0]
    assert len(rows(db, "active_tasks")) == 1


def test_missing_entries_and_no_gateway_do_not_break_sampling() -> None:
    sampler, db = make(Clock(), poller())
    assert sampler.sample_once() == 0 and rows(db) == []


def test_gateway_counters_are_summed_per_virtual_key() -> None:
    p = poller()
    p.gateway_snapshot = GatewaySnapshot(
        0.0,
        [
            Row("query", "m1", "openai", requests=2, input_tokens=10, output_tokens=5, cost=0.5, errors=0),
            Row("query", "m2", "openai", requests=3, input_tokens=1, output_tokens=1, cost=0.25, errors=1),
            Row("tlr", "m1", "openai", requests=1),
        ],
        None,
    )
    sampler, db = make(Clock(), p)
    sampler.sample_once()
    got = {(s, k): v for _, s, k, v in rows(db)}
    assert got[("llm_requests", "query")] == 5 and got[("llm_tokens_in", "query")] == 11
    assert got[("llm_cost", "query")] == 0.75 and got[("llm_errors", "query")] == 1
    assert got[("llm_requests", "tlr")] == 1 and ("llm_cost", "tlr") not in got


def test_prune_removes_exactly_rows_older_than_retention() -> None:
    clock = Clock(10 * DAY)
    sampler, db = make(clock, poller())
    old, edge, fresh = int(clock.t - 14 * DAY) - 1, int(clock.t - 14 * DAY), int(clock.t) - 5
    db.executemany("INSERT INTO samples VALUES (?, 'health', 'x', 1)", [(old,), (edge,), (fresh,)])
    assert sampler.prune() == 1
    assert [r[0] for r in rows(db)] == [edge, fresh]


def test_sampling_prunes_hourly() -> None:
    clock = Clock(30 * DAY)
    p = poller(a=Entry("a", "service", "up"))
    sampler, db = make(clock, p)
    db.execute("INSERT INTO samples VALUES (?, 'health', 'old', 1)", (int(clock.t - 15 * DAY),))
    sampler.sample_once()
    assert [r[2] for r in rows(db)] == ["a"]
    db.execute("INSERT INTO samples VALUES (?, 'health', 'old', 1)", (int(clock.t - 15 * DAY),))
    clock.t += 60
    sampler.sample_once()
    assert "old" in {r[2] for r in rows(db)}
    clock.t += 3600
    sampler.sample_once()
    assert "old" not in {r[2] for r in rows(db)}
