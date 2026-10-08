import sqlite3

from dash_fakes import Env
from sandman_dashboard.sampler import MAX_POINTS, history
from sandman_dashboard.store import SessionStore

NOW = 2_000_000
DAY = 86400


def db_with(rows: list[tuple[int, str, str, float]]) -> sqlite3.Connection:
    db = SessionStore(":memory:").db
    db.executemany("INSERT INTO samples VALUES (?, ?, ?, ?)", rows)
    return db


def test_downsampling_keeps_at_most_1000_points_and_min_max_of_health() -> None:
    step = 30
    rows = [(NOW - 14 * DAY + i * step, "health", "svc", 2.0) for i in range(14 * DAY // step)]
    dip = rows[12345][0]
    rows[12345] = (dip, "health", "svc", 0.0)
    rows += [(t, "active_tasks", "svc", float(i % 5)) for i, (t, *_) in enumerate(rows[:5000])]
    result = history(db_with(rows), "14d", NOW)
    points = result["series"]["health"]["svc"]
    assert 0 < len(points) <= MAX_POINTS
    assert min(v for _, v in points) == 0.0 and max(v for _, v in points) == 2.0
    assert all(a[0] <= b[0] for a, b in zip(points, points[1:], strict=False))
    assert len(result["series"]["active_tasks"]["svc"]) <= MAX_POINTS


def test_short_range_returns_raw_points() -> None:
    rows = [(NOW - 3000 + i * 30, "health", "svc", 2.0) for i in range(100)]
    points = history(db_with(rows), "1h", NOW)["series"]["health"]["svc"]
    assert len(points) == 100 and points[0] == [NOW - 3000, 2.0]


def test_counter_deltas_and_reset_are_not_negative() -> None:
    values = [10, 15, 15, 40, 3, 8]
    rows = [(NOW - 3000 + i * 30, "llm_requests", "query", float(v)) for i, v in enumerate(values)]
    points = history(db_with(rows), "1h", NOW)["series"]["llm_requests"]["query"]
    assert [v for _, v in points] == [5, 0, 25, 3, 5]
    assert all(v >= 0 for _, v in points)


def test_counter_deltas_are_summed_per_bucket_when_downsampled() -> None:
    rows = [(NOW - 14 * DAY + i * 30, "llm_cost", "query", i * 0.01) for i in range(14 * DAY // 30)]
    points = history(db_with(rows), "14d", NOW)["series"]["llm_cost"]["query"]
    assert len(points) <= MAX_POINTS
    assert abs(sum(v for _, v in points) - (len(rows) - 1) * 0.01) < 1.0


def test_unknown_series_and_empty_db() -> None:
    result = history(db_with([]), "24h", NOW)
    assert result["series"]["health"] == {} and result["to"] - result["from"] == DAY


async def test_endpoint_requires_a_session_and_validates_range(env: Env) -> None:
    assert (await env.http.get("/api/history")).status_code == 401
    assert (await env.http.get("/history")).status_code == 303
    await env.login()
    ok = await env.http.get("/api/history", params={"range": "1h"})
    assert ok.status_code == 200 and ok.json()["range"] == "1h" and "health" in ok.json()["series"]
    assert (await env.http.get("/api/history", params={"range": "5y"})).status_code == 422
    page = (await env.http.get("/history", params={"range": "14d"})).text
    assert 'data-range="14d"' in page and "/static/uplot.min.js" in page and "/static/history.js" in page


async def test_history_after_a_sample(env: Env) -> None:
    await env.login()
    await env.app.state.poller.poll_once()
    assert env.app.state.sampler.sample_once() > 0
    series = (await env.http.get("/api/history", params={"range": "1h"})).json()["series"]
    assert {"orchestrator", "tlr-agent", "memgraph", "nats", "llm-gateway"} <= series["health"].keys()
