import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from dash_fakes import Env
from sandman_dashboard.alerts import AlertEngine, memory_ratio, parse_size
from sandman_dashboard.gateway import Budget
from sandman_dashboard.poller import Entry

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def engine(clock: Clock) -> AlertEngine:
    return AlertEngine(clock, lambda: NOW)


def entries(**health: str) -> dict[str, Entry]:
    return {n: Entry(n, "service", h) for n, h in health.items()}


def lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [r.__dict__["fields"] for r in caplog.records if r.__dict__.get("fields", {}).get("event") == "alert"]


def wf(wf_id: str, status: str, age: timedelta = timedelta(hours=1)) -> dict[str, str]:
    return {"id": wf_id, "status": status, "updatedAt": (NOW - age).isoformat()}


def test_down_for_29_seconds_raises_nothing_and_31_raises_once(
    engine: AlertEngine, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    down = entries(tlr="down", nats="up")
    engine.evaluate(down, [], [], [])
    clock.t += 29
    engine.evaluate(down, [], [], [])
    assert engine.active == {} and lines(caplog) == []
    clock.t += 2
    engine.evaluate(down, [], [], [])
    clock.t += 60
    engine.evaluate(down, [], [], [])
    assert list(engine.active) == ["down:tlr"] and engine.active["down:tlr"].href == "/"
    assert [(x["alert"], x["key"], x["state"]) for x in lines(caplog)] == [("service_down", "down:tlr", "raised")]
    engine.evaluate(entries(tlr="up", nats="up"), [], [], [])
    engine.evaluate(entries(tlr="up", nats="up"), [], [], [])
    assert engine.active == {} and [x["state"] for x in lines(caplog)] == ["raised", "cleared"]


def test_short_outage_restarts_the_timer(engine: AlertEngine, clock: Clock) -> None:
    engine.evaluate(entries(a="down"), [], [], [])
    clock.t += 20
    engine.evaluate(entries(a="up"), [], [], [])
    clock.t += 20
    engine.evaluate(entries(a="down"), [], [], [])
    clock.t += 20
    engine.evaluate(entries(a="down"), [], [], [])
    assert engine.active == {}


def test_workflow_alerts_per_id_window_and_clear(engine: AlertEngine, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    problems = [wf("wf_1", "FAILED"), wf("wf_2", "BLOCKED"), wf("wf_old", "FAILED", timedelta(hours=25))]
    engine.evaluate({}, problems, [], [])
    engine.evaluate({}, problems, [], [])
    assert set(engine.active) == {"workflow:wf_1", "workflow:wf_2"}
    assert engine.active["workflow:wf_1"].href == "/workflows/wf_1"
    engine.evaluate({}, [wf("wf_2", "BLOCKED")], [], [])
    assert set(engine.active) == {"workflow:wf_2"}
    assert [(x["key"], x["state"]) for x in lines(caplog)] == [
        ("workflow:wf_1", "raised"),
        ("workflow:wf_2", "raised"),
        ("workflow:wf_1", "cleared"),
    ]


def test_unknown_data_keeps_alerts(engine: AlertEngine) -> None:
    engine.evaluate({}, [wf("wf_1", "FAILED")], [{"eventId": "e"}], [])
    engine.evaluate({}, None, None, None)
    assert set(engine.active) == {"workflow:wf_1", "dead-events"}


def test_dead_events_and_budgets(engine: AlertEngine, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    budgets = [Budget("vk-a", "a", 10, 8.0), Budget("vk-b", "b", 10, 7.9)]
    engine.evaluate({}, [], [{"eventId": "e1"}, {"eventId": "e2"}], budgets)
    engine.evaluate({}, [], [{"eventId": "e1"}], budgets)
    assert set(engine.active) == {"dead-events", "budget:a"}
    assert engine.active["dead-events"].message == "1 dead events"
    engine.evaluate({}, [], [], [Budget("vk-a", "a", 10, 1.0)])
    assert engine.active == {}
    assert [(x["alert"], x["state"]) for x in lines(caplog)] == [
        ("dead_events", "raised"),
        ("budget", "raised"),
        ("dead_events", "cleared"),
        ("budget", "cleared"),
    ]


@pytest.mark.parametrize(
    ("memory", "limit", "raised"),
    [
        ("6.39GiB", "8.00GiB", False),
        ("6.40GiB", "8.00GiB", True),
        ("7168MiB", "unlimited", False),
        ("1GiB", None, False),
    ],
)
def test_memgraph_memory(engine: AlertEngine, memory: str, limit: str | None, raised: bool) -> None:
    mem = Entry("memgraph", "infra", "up", details={"memory": memory, **({"memoryLimit": limit} if limit else {})})
    engine.evaluate({"memgraph": mem}, [], [], [])
    assert bool(engine.active) is raised


def test_parse_size() -> None:
    assert parse_size("2.00MiB") == 2 * 1024**2 and parse_size("8.00GiB") == 8 * 1024**3
    assert parse_size(1024) == 1024 and parse_size("n/a") is None and parse_size(True) is None
    assert memory_ratio({"memory": "1GiB", "memoryLimit": "4GiB"}) == 0.25


async def test_log_lines_are_json_and_banner_shows_active_alerts(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    from sandman_agent.telemetry import JsonFormatter

    caplog.set_level(logging.INFO)
    await env.login()
    t = [0.0]
    poller = env.app.state.poller
    poller.alerts.clock = lambda: t[0]
    env.upstream.down.add("tlr-agent")
    await poller.poll_once()
    assert "tlr-agent down" not in (await env.http.get("/")).text
    t[0] = 31
    await poller.poll_once()
    page = await env.http.get("/")
    assert 'id="alerts"' in page.text and "tlr-agent down for 31 s" in page.text
    assert "tlr-agent down" in (await env.http.get("/fragments/alerts")).text
    record = next(r for r in caplog.records if r.__dict__.get("fields", {}).get("alert") == "service_down")
    line = json.loads(JsonFormatter("dashboard").format(record))
    assert line["event"] == "alert" and line["alert"] == "service_down" and line["state"] == "raised"
    assert line["key"] == "down:tlr-agent" and "message" in line
    env.upstream.down.clear()
    await poller.poll_once()
    assert "tlr-agent down" not in (await env.http.get("/fragments/alerts")).text
