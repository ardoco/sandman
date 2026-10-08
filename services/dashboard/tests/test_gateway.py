from pathlib import Path

import httpx
import pytest
from sandman_dashboard.gateway import GatewayClient, Row, parse_budgets, parse_metrics, quantile, summarize

FIXTURE = Path(__file__).with_name("bifrost-metrics.txt").read_text()


def by_group(text: str = FIXTURE) -> dict[tuple[str, str, str], Row]:
    return {(r.key, r.model, r.provider): r for r in summarize(parse_metrics(text))}


def test_totals_per_key_model_provider() -> None:
    rows = by_group()
    assert set(rows) == {
        ("query", "gpt-4.1-nano", "openai"),
        ("tlr", "llama3", "ollama"),
        ("tlr", "gpt-4.1-mini", "openai"),
    }
    query = rows[("query", "gpt-4.1-nano", "openai")]
    assert (query.requests, query.input_tokens, query.output_tokens, query.errors) == (5, 1200, 300, 1)
    assert query.cost == pytest.approx(0.0006)
    assert rows[("tlr", "llama3", "ollama")].errors == 0
    assert rows[("tlr", "gpt-4.1-mini", "openai")].errors == 1


def test_latency_percentiles_from_histogram() -> None:
    query = by_group()[("query", "gpt-4.1-nano", "openai")]
    assert 0.25 < query.p50 < 1.0 < query.p95 <= 2.5


def test_absent_metric_is_none() -> None:
    text = "\n".join(line for line in FIXTURE.splitlines() if not line.startswith("bifrost_cost_total"))
    rows = by_group(text)
    assert all(r.cost is None for r in rows.values())
    assert all(r.requests is not None for r in rows.values())
    assert all(r.p95 is None for r in by_group("bifrost_active_requests 0\n").values())


def test_parser_handles_escapes_and_garbage() -> None:
    samples = parse_metrics(
        'go_goroutines 3\nbifrost_x{a="q\\"uote",b="x"} 1.5e1 1700000000\nbifrost_bad{a="b"} nope\nbifrost_y 2\n'
    )
    assert [(s.name, dict(s.labels), s.value) for s in samples] == [
        ("bifrost_x", {"a": 'q"uote', "b": "x"}, 15.0),
        ("bifrost_y", {}, 2.0),
    ]


def test_quantile_edges() -> None:
    assert quantile(0.5, []) is None
    assert quantile(0.95, [(1.0, 0.0), (float("inf"), 0.0)]) is None
    assert quantile(0.5, [(1.0, 10.0), (float("inf"), 10.0)]) == 0.5
    assert quantile(0.99, [(1.0, 1.0), (float("inf"), 2.0)]) == 1.0


def test_budgets_and_warning() -> None:
    doc = {"budgets": [{"virtual_key_id": "vk-a", "max_limit": 10, "current_usage": 8.5}, {"bad": 1}]}
    [budget] = parse_budgets(doc, {"vk-a": "a"})
    assert (budget.key_name, budget.ratio, budget.warn) == ("a", 0.85, True)


async def test_client_fetch_sends_basic_auth_and_computes_rates() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        if request.url.path == "/metrics":
            return httpx.Response(200, text=FIXTURE)
        return httpx.Response(500, text="secret-body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GatewayClient("http://gw:8080", "pw", http)
        first = await client.fetch(None)
        second = await client.fetch(first)
    assert first.budgets is None and all(a.startswith("Basic ") for a in seen)
    assert all(r.rates["requests"] is None or r.rates["requests"] == 0 for r in first.rows[:1])
    assert second.rows[0].rates["requests"] == 0
