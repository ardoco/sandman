"""Console graph explorer (T6.5) against a running stack: `make e2e` runs it after the walking-skeleton test.

It needs the e2e project of the walking skeleton.
Environment: SANDMAN_E2E=1 (required), SANDMAN_E2E_URL (default http://127.0.0.1:8080),
SANDMAN_E2E_ADMIN_NEW_PASSWORD (default: content of $SANDMAN_E2E_SECRETS/e2e_admin_password).
"""

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("SANDMAN_E2E"), reason="set SANDMAN_E2E=1 with the stack running")

ROOT = Path(__file__).resolve().parents[2]
URL = os.environ.get("SANDMAN_E2E_URL", "http://127.0.0.1:8080")
CSRF = re.compile(r'name="csrf" value="([^"]+)"')


def admin_password() -> str:
    if value := os.environ.get("SANDMAN_E2E_ADMIN_NEW_PASSWORD"):
        return value
    secrets_dir = Path(os.environ.get("SANDMAN_E2E_SECRETS", ROOT / "deploy" / "secrets"))
    return (secrets_dir / "e2e_admin_password").read_text().strip()


@pytest.fixture(scope="module")
def console() -> Iterator[httpx.Client]:
    http = httpx.Client(base_url=URL, timeout=60)
    csrf = CSRF.search(http.get("/console/login").text)
    assert csrf
    login = http.post("/console/login", data={"csrf": csrf[1], "username": "admin", "password": admin_password()})
    assert login.status_code == 303, login.text
    yield http
    http.close()


def nodes(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [e["data"] for e in body["elements"] if e["group"] == "nodes"]


def test_graph_of_the_e2e_project(console: httpx.Client) -> None:
    listing = console.get("/console/projects")
    projects = re.findall(r"/console/projects/(e2e-[A-Za-z0-9-]+)/graph", listing.text)
    assert projects, "no e2e project on the projects page"
    base = f"/console/projects/{projects[-1]}/graph"

    page = console.get(base)
    assert page.status_code == 200 and "/static/graph.js" in page.text
    assert page.headers["content-security-policy"] == "default-src 'self'"
    artifacts = re.findall(r'<option value="(art_[0-9a-f]+)">', page.text)
    assert artifacts

    whole = console.get(f"{base}/start", params={"labels": "Requirement"}).json()
    requirements = nodes(whole)
    assert len(requirements) >= 3 and all("Requirement" in n["labels"] for n in requirements)

    one = console.get(f"{base}/start", params={"artifact": artifacts[0], "state": "current"}).json()
    assert nodes(one) and {n["props"]["projectId"] for n in nodes(one) if "projectId" in n["props"]} == {projects[-1]}

    expanded = console.get(f"{base}/expand", params={"id": requirements[0]["nid"], "hops": 2}).json()
    assert len(nodes(expanded)) > 1

    token = re.search(r'data-csrf="([^"]+)"', page.text)
    assert token
    headers = {"x-csrf-token": token[1]}
    path = console.post(
        f"{base}/query",
        json={"cypher": "MATCH p = (a:Requirement {projectId: $projectId})-[*1..3]-(b:CodeUnit) RETURN p LIMIT 1"},
        headers=headers,
    )
    assert path.status_code == 200, path.text
    write = console.post(f"{base}/query", json={"cypher": "CREATE (n:E2eEvil) RETURN n"}, headers=headers)
    assert write.status_code == 422, write.text
    check = console.post(
        f"{base}/query", json={"cypher": "MATCH (n:E2eEvil {projectId: $projectId}) RETURN count(n)"}, headers=headers
    )
    assert check.status_code == 200 and check.json()["rows"] == [[0]]
