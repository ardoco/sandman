"""Observability smoke test: run after the walking skeleton against a stack started with `--profile obs`.

Environment: SANDMAN_E2E_OBS=1 (required), SANDMAN_E2E_GRAFANA_URL (default http://127.0.0.1:3001), Grafana admin
credentials admin/admin (otel-lgtm default). Tempo and Prometheus are reached through the Grafana datasource proxy.
"""

import os
import time

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SANDMAN_E2E_OBS"), reason="set SANDMAN_E2E_OBS=1 with the obs stack"
)

GRAFANA = os.environ.get("SANDMAN_E2E_GRAFANA_URL", "http://127.0.0.1:3001")
FIVE = {"query-service", "extraction-agent", "orchestrator", "tlr-agent", "inconsistency-agent"}
DASHBOARDS = {"sandman-a2a", "sandman-events", "sandman-llm-tokens"}


@pytest.fixture(scope="module")
def grafana() -> httpx.Client:
    return httpx.Client(base_url=GRAFANA, auth=("admin", "admin"), timeout=30)


def services_of(trace: dict) -> set[str]:
    found = set()
    for batch in trace.get("batches", []):
        for attr in batch["resource"]["attributes"]:
            if attr["key"] == "service.name":
                found.add(attr["value"]["stringValue"])
    return found


def test_dashboards_are_provisioned(grafana: httpx.Client) -> None:
    found = {d["uid"] for d in grafana.get("/api/search", params={"tag": "sandman"}).json()}
    assert DASHBOARDS <= found
    for uid in DASHBOARDS:
        assert grafana.get(f"/api/dashboards/uid/{uid}").status_code == 200


def test_one_trace_spans_all_services(grafana: httpx.Client) -> None:
    deadline = time.monotonic() + 60
    best: set[str] = set()
    while time.monotonic() < deadline:
        hits = grafana.get(
            "/api/datasources/proxy/uid/tempo/api/search",
            params={"q": '{ resource.service.name = "tlr-agent" }', "limit": 20},
        ).json()
        for hit in hits.get("traces", []):
            trace = grafana.get(f"/api/datasources/proxy/uid/tempo/api/traces/{hit['traceID']}").json()
            best = max(best, services_of(trace), key=len)
            if FIVE <= best:
                return
        time.sleep(5)
    pytest.fail(f"no trace with all of {sorted(FIVE)}; best: {sorted(best)}")


def test_a2a_metric_is_queryable(grafana: httpx.Client) -> None:
    resp = grafana.get(
        "/api/datasources/proxy/uid/prometheus/api/v1/query",
        params={"query": "sum(sandman_a2a_task_duration_seconds_count)"},
    )
    assert resp.json()["data"]["result"]
