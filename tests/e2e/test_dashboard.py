"""Dashboard (T1.9-T1.11) against a running stack: `make e2e` runs it after the walking-skeleton test.

It needs the project of the walking skeleton.
Environment: SANDMAN_E2E=1 (required), SANDMAN_E2E_RESOURCES=1 when the compose profile `resources` runs,
SANDMAN_E2E_DASHBOARD_URL (default http://127.0.0.1:8090),
SANDMAN_E2E_DASHBOARD_PASSWORD (default: content of $SANDMAN_E2E_SECRETS/dashboard_admin_password, secrets dir default
deploy/secrets). The test stops and restarts tlr-agent with `docker compose -p <project>` (SANDMAN_E2E_PROJECT, else
COMPOSE_PROJECT_NAME, else "sandman" as named in deploy/docker-compose.yml). With a non-default SANDMAN_E2E_URL or
SANDMAN_E2E_DASHBOARD_URL the project must be set explicitly, otherwise the test fails before touching docker.
"""

import os
import re
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("SANDMAN_E2E"), reason="set SANDMAN_E2E=1 with the stack running")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "http://127.0.0.1:8090"
URL = os.environ.get("SANDMAN_E2E_DASHBOARD_URL", DEFAULT_URL)
CSRF = re.compile(r'name="csrf" value="([^"]+)"')
TARGETS = {
    "extraction-agent",
    "tlr-agent",
    "inconsistency-agent",
    "orchestrator",
    "query-service",
    "llm-replay",
    "document-converter",
}
INFRASTRUCTURE = {"memgraph", "nats", "llm-gateway"}


def compose_command() -> list[str]:
    project = os.environ.get("SANDMAN_E2E_PROJECT") or os.environ.get("COMPOSE_PROJECT_NAME")
    if not project:
        skeleton_url = os.environ.get("SANDMAN_E2E_URL", "http://127.0.0.1:8080")
        scratch = URL != DEFAULT_URL or skeleton_url != "http://127.0.0.1:8080"
        if scratch:
            pytest.fail("set COMPOSE_PROJECT_NAME (or SANDMAN_E2E_PROJECT) to the project of the stack under test")
        project = "sandman"
    return [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(ROOT / "deploy" / "docker-compose.yml"),
        "--profile",
        "stubs",
        "--profile",
        "dashboard",
    ]


def password() -> str:
    if value := os.environ.get("SANDMAN_E2E_DASHBOARD_PASSWORD"):
        return value
    secrets_dir = Path(os.environ.get("SANDMAN_E2E_SECRETS", ROOT / "deploy" / "secrets"))
    return (secrets_dir / "dashboard_admin_password").read_text().strip()


@pytest.fixture(scope="module")
def dashboard() -> Iterator[httpx.Client]:
    http = httpx.Client(base_url=URL, timeout=30)
    assert http.get("/readyz").status_code == 200, f"dashboard not ready at {URL}"
    page = http.get("/login")
    csrf = CSRF.search(page.text)
    assert csrf
    login = http.post("/login", data={"csrf": csrf[1], "username": "admin", "password": password()})
    assert login.status_code == 303 and login.headers["location"] == "/", login.text
    yield http
    http.close()


def services(http: httpx.Client) -> dict[str, dict[str, Any]]:
    resp = http.get("/api/services")
    assert resp.status_code == 200, resp.text
    return {s["name"]: s for s in resp.json()["services"]}


def wait_for(check: Callable[[], bool], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(1)
    pytest.fail(f"timeout waiting for {what}")


def test_protected_without_session() -> None:
    anonymous = httpx.Client(base_url=URL, timeout=30)
    assert anonymous.get("/").headers["location"] == "/login"
    assert anonymous.get("/api/services").status_code == 401


def test_everything_is_up(dashboard: httpx.Client) -> None:
    def all_up() -> bool:
        found = services(dashboard)
        return (TARGETS | INFRASTRUCTURE) <= found.keys() and all(found[n]["health"] == "up" for n in found)

    wait_for(all_up, 90, "all targets and infrastructure up")
    found = services(dashboard)
    assert set(found) == TARGETS | INFRASTRUCTURE
    assert "ollama" not in found
    assert found["nats"]["details"].get("stream", {}).get("messages") is not None
    assert found["memgraph"]["details"]["nodes"] > 0
    page = dashboard.get("/")
    assert page.status_code == 200 and "tlr-agent" in page.text and "llm-gateway" in page.text


def test_graph_of_the_e2e_project_and_read_only(dashboard: httpx.Client) -> None:
    projects = [p["id"] for p in dashboard.get("/api/graph/projects").json()["projects"]]
    e2e = [p for p in projects if p.startswith("e2e-")]
    assert e2e, f"no e2e project in {projects}"
    graph = dashboard.get("/api/graph/start", params={"project": e2e[-1], "label": "Requirement"}).json()
    nodes = [e for e in graph["elements"] if e["group"] == "nodes"]
    assert len(nodes) >= 3 and not graph["truncated"]
    assert all("Requirement" in n["data"]["labels"] for n in nodes)

    page = dashboard.get("/graph")
    csrf = CSRF.search(page.text)
    assert csrf
    headers = {"x-csrf-token": csrf[1]}
    write = dashboard.post("/api/cypher", json={"cypher": "CREATE (n:E2eEvil) RETURN n"}, headers=headers)
    assert write.status_code == 422, write.text
    check = dashboard.post("/api/cypher", json={"cypher": "MATCH (n:E2eEvil) RETURN count(n) AS c"}, headers=headers)
    assert check.status_code == 200 and check.json()["rows"] == [[0]]
    assert dashboard.post("/api/cypher", json={"cypher": "RETURN 1"}).status_code == 403


def test_gateway_page_renders(dashboard: httpx.Client) -> None:
    page = dashboard.get("/gateway")
    assert page.status_code == 200 and "<h2>Budgets</h2>" in page.text
    stats = dashboard.get("/api/gateway").json()
    assert stats["available"] is True
    # structure, not traffic: in replay mode the counters may be zero, but the gateway lists its keys/budgets
    assert stats["rows"] or stats["budgets"], stats
    for row in stats["rows"]:
        assert {"key", "model", "provider", "requests"} <= row.keys(), row
    for budget in stats["budgets"] or []:
        assert {"key", "limit", "usage", "warn"} <= budget.keys() and budget["limit"] > 0, budget


def test_every_service_reports_working_or_idle_and_settles_to_idle(dashboard: httpx.Client) -> None:
    for name, entry in services(dashboard).items():
        if name in TARGETS:
            assert entry["state"] in ("working", "idle"), (name, entry)
            assert isinstance(entry["active_tasks"], int) and entry["active_tasks"] >= 0, (name, entry)

    def idle() -> bool:
        found = services(dashboard)
        return all(found[n]["state"] == "idle" and found[n]["active_tasks"] == 0 for n in TARGETS)

    wait_for(idle, 120, "all services idle after the walking skeleton")
    assert dashboard.get("/").text.count("<td>idle</td>") >= len(TARGETS)


def test_stopped_agent_is_shown_down_and_recovers(dashboard: httpx.Client) -> None:
    compose = compose_command()
    subprocess.run([*compose, "stop", "tlr-agent"], check=True, cwd=ROOT, capture_output=True)
    try:
        wait_for(lambda: services(dashboard)["tlr-agent"]["health"] == "down", 30, "tlr-agent down")
        assert services(dashboard)["orchestrator"]["health"] == "up"
    finally:
        subprocess.run(
            [*compose, "start", "--wait", "--wait-timeout", "180", "tlr-agent"],
            check=True,
            cwd=ROOT,
            capture_output=True,
        )
    wait_for(lambda: services(dashboard)["tlr-agent"]["health"] == "up", 60, "tlr-agent up again")


def test_plugin_stubs_restart_becomes_healthy_again() -> None:
    """`start --wait` only returns once the plugin-stubs healthcheck (extract 8000 and detect 8001) passes."""
    compose = compose_command()
    subprocess.run([*compose, "stop", "plugin-stubs"], check=True, cwd=ROOT, capture_output=True)
    subprocess.run(
        [*compose, "start", "--wait", "--wait-timeout", "120", "plugin-stubs"],
        check=True,
        cwd=ROOT,
        capture_output=True,
    )
    ps = subprocess.run(
        [*compose, "ps", "--format", "{{.Health}}", "plugin-stubs"],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert ps.stdout.strip() == "healthy", ps.stdout


def e2e_project(http: httpx.Client) -> str:
    projects = [p["id"] for p in http.get("/api/graph/projects").json()["projects"]]
    e2e = [p for p in projects if p.startswith("e2e-")]
    assert e2e, f"no e2e project in {projects}"
    return e2e[-1]


def test_workflows_page_lists_succeeded_workflows(dashboard: httpx.Client) -> None:
    project = e2e_project(dashboard)
    page = dashboard.get("/workflows", params={"projectId": project})
    assert page.status_code == 200 and "orchestrator unreachable" not in page.text
    rows = re.findall(r'<td><a href="/workflows/(wf_[0-9a-f]{32})">', page.text)
    assert rows and 'class="status-SUCCEEDED">SUCCEEDED' in page.text
    detail = dashboard.get(f"/workflows/{rows[0]}")
    assert detail.status_code == 200 and "Cancel workflow" not in detail.text and "-agent" in detail.text


def test_events_page_shows_the_ingestion_event(dashboard: httpx.Client) -> None:
    project = e2e_project(dashboard)

    def seen() -> bool:
        return "sandman.ingestion.completed" in dashboard.get("/events", params={"project": project}).text

    wait_for(seen, 30, "ingestion event in the viewer")
    page = dashboard.get("/events", params={"project": project, "type": "sandman.ingestion.completed"})
    seq = re.search(r'<a href="/events/(\d+)">', page.text)
    assert seq and project in dashboard.get(f"/events/{seq[1]}").text


def test_experiments_page_shows_llm_mode(dashboard: httpx.Client) -> None:
    mode = os.environ.get("SANDMAN_LLM_MODE", "live")
    assert f"<th>Mode</th><td>{mode}</td>" in dashboard.get("/experiments").text


def test_state_changing_requests_need_csrf(dashboard: httpx.Client) -> None:
    assert dashboard.post(f"/workflows/wf_{'0' * 32}/cancel", data={"csrf": "x"}).status_code == 403
    assert dashboard.post("/dead-events/replay", data={"eventId": "x"}).status_code == 403


def test_projects_page_shows_the_e2e_project_figures(dashboard: httpx.Client) -> None:
    project = e2e_project(dashboard)
    row = re.search(rf'<a href="/projects/{project}">.*?</tr>', dashboard.get("/projects").text, re.S)
    assert row, "project row missing"
    cells = re.findall(r"<td>([^<]*)</td>", row[0])
    artifacts = re.match(r"(\d+) / (\d+)", cells[1])
    assert artifacts and int(artifacts[1]) > 0
    assert all(cells[i].replace(",", "").isdigit() and int(cells[i].replace(",", "")) > 0 for i in (2, 3, 4, 5)), cells
    detail = dashboard.get(f"/projects/{project}").text
    assert "<td>Requirement</td>" in detail and "n/a" not in detail


def test_history_has_a_sample_per_service(dashboard: httpx.Client) -> None:
    expected = TARGETS | INFRASTRUCTURE

    def sampled() -> bool:
        health = dashboard.get("/api/history", params={"range": "1h"}).json()["series"]["health"]
        return expected <= health.keys()

    wait_for(sampled, 120, "one health sample per service")
    assert dashboard.get("/history").status_code == 200


def test_resources_page(dashboard: httpx.Client) -> None:
    page = dashboard.get("/resources")
    assert page.status_code == 200
    if not os.environ.get("SANDMAN_E2E_RESOURCES"):
        assert "not active" in page.text or "<td>orchestrator</td>" in page.text
        return
    for service in ("memgraph", "nats", "orchestrator", "dashboard", "cadvisor"):
        assert f"<td>{service}</td>" in page.text, service
