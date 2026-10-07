"""Walking skeleton (Stage 2 §5) against a running stack: `make e2e`, or compose --profile stubs up and SANDMAN_E2E=1.

Environment: SANDMAN_E2E=1 (required), SANDMAN_E2E_URL (default http://127.0.0.1:8080),
SANDMAN_E2E_ADMIN_PASSWORD (default: content of $SANDMAN_E2E_SECRETS/admin_bootstrap_password, secrets dir default
deploy/secrets). The admin account is forced to change its bootstrap password on first use; the new password comes
from SANDMAN_E2E_ADMIN_NEW_PASSWORD or $SANDMAN_E2E_SECRETS/e2e_admin_password (created by deploy/gen-secrets.sh).
Every run uses its own project and users.
"""

import json
import os
import re
import secrets
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sandman_cli.__main__ import main as sandman

pytestmark = pytest.mark.skipif(not os.environ.get("SANDMAN_E2E"), reason="set SANDMAN_E2E=1 with the stack running")

ROOT = Path(__file__).resolve().parents[2]
MINI = Path(__file__).parent / "fixtures" / "mini"
URL = os.environ.get("SANDMAN_E2E_URL", "http://127.0.0.1:8080")
CSRF = re.compile(r'name="csrf" value="([^"]+)"')
WORKFLOW_TIMEOUT = 240.0


def console_token(username: str, initial: str, new: str, reused: bool = False) -> str:
    """Personal API token via the console: the API accepts bearer tokens only and new accounts must change their
    password. `reused` accounts may already have `new` (a failed probe counts against the login rate limit)."""

    def login(password: str) -> tuple[httpx.Client, httpx.Response]:
        http = httpx.Client(base_url=URL, timeout=30)
        csrf = CSRF.search(http.get("/console/login").text)
        assert csrf
        return http, http.post("/console/login", data={"csrf": csrf[1], "username": username, "password": password})

    http, resp = login(new) if reused else (httpx.Client(), httpx.Response(401))
    if resp.status_code == 401:
        http, resp = login(initial)
        assert resp.headers["location"] == "/console/password", resp.text
        csrf = CSRF.search(http.get("/console/password").text)
        assert csrf
        resp = http.post("/console/password", data={"csrf": csrf[1], "current": initial, "new": new})
        assert resp.status_code == 303
        http, resp = login(new)
    assert resp.status_code == 303 and resp.headers["location"] == "/console/projects", resp.text
    csrf = CSRF.search(http.get("/console/tokens").text)
    assert csrf
    page = http.post("/console/tokens", data={"csrf": csrf[1], "name": "e2e"}).text
    token = re.search(r"<code>([^<]+)</code>", page)
    assert token
    return token[1]


def secret(env: str, name: str) -> str:
    if value := os.environ.get(env):
        return value
    secrets_dir = Path(os.environ.get("SANDMAN_E2E_SECRETS", ROOT / "deploy" / "secrets"))
    return (secrets_dir / name).read_text().strip()


def client(token: str) -> httpx.Client:
    return httpx.Client(base_url=URL, headers={"Authorization": f"Bearer {token}"}, timeout=120)


class Stack:
    def __init__(self) -> None:
        suffix = secrets.token_hex(3)
        self.project = f"e2e-{suffix}"
        admin = client(
            console_token(
                "admin",
                secret("SANDMAN_E2E_ADMIN_PASSWORD", "admin_bootstrap_password"),
                secret("SANDMAN_E2E_ADMIN_NEW_PASSWORD", "e2e_admin_password"),
                reused=True,
            )
        )
        admin.put(
            f"/v1/admin/projects/{self.project}", json={"name": "Mini", "llmPolicy": "local-only"}
        ).raise_for_status()
        self.users: dict[str, httpx.Client] = {}
        for name, role in (("alice", "maintainer"), ("bob", "triager"), ("carol", "viewer")):
            user = f"{name}-{suffix}"
            initial = f"initial-{secrets.token_hex(8)}"
            admin.post("/v1/admin/users", json={"username": user, "password": initial}).raise_for_status()
            admin.put(f"/v1/admin/projects/{self.project}/members/{user}", json={"role": role}).raise_for_status()
            self.users[name] = client(console_token(user, initial, f"changed-{secrets.token_hex(8)}"))
        self.admin = admin
        self.alice_token = str(self.users["alice"].headers["Authorization"]).removeprefix("Bearer ")

    def ingest(self, snapshot: str, revision: str, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
        """Runs `sandman ingest` (CLI) with alice's project token and returns the printed Ingestion."""
        os.environ.update(SANDMAN_URL=URL, SANDMAN_TOKEN=self.alice_token)
        capsys.readouterr()
        args = ["ingest", "--project", self.project, "--mode", "full", "--revision", revision, str(MINI / snapshot)]
        assert sandman(args) == 0
        out: dict[str, Any] = json.loads(capsys.readouterr().out)
        return out

    def workflows(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = self.users["alice"].get(f"/v1/projects/{self.project}/workflows").json()["items"]
        return items

    def wait_workflow(self, known: set[str]) -> dict[str, Any]:
        """Waits for the next workflow (not in `known`) to succeed and returns it with its steps."""
        deadline = time.monotonic() + WORKFLOW_TIMEOUT
        while time.monotonic() < deadline:
            new = [w for w in self.workflows() if w["id"] not in known]
            if new and all(w["status"] in ("SUCCEEDED", "FAILED", "CANCELED", "BLOCKED") for w in new):
                assert [w["status"] for w in new] == ["SUCCEEDED"] * len(new), new
                known.update(w["id"] for w in new)
                detail: dict[str, Any] = self.users["alice"].get(f"/v1/workflows/{new[0]['id']}").json()
                return detail
            time.sleep(2)
        pytest.fail(f"no finished workflow within {WORKFLOW_TIMEOUT}s; known={known}")

    def cypher(self, who: str, text: str, **extra: Any) -> httpx.Response:
        return self.users[who].post("/v1/query/cypher", json={"projectId": self.project, "cypher": text, **extra})

    def rows(self, text: str) -> list[list[Any]]:
        resp = self.cypher("alice", text)
        assert resp.status_code == 200, resp.text
        rows: list[list[Any]] = resp.json()["rows"]
        return rows

    def inconsistencies(self) -> dict[str, dict[str, Any]]:
        items = self.users["alice"].get(f"/v1/projects/{self.project}/inconsistencies").json()["items"]
        return {i["title"].rsplit(": ", 1)[1]: i for i in items}


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    health = httpx.get(f"{URL}/readyz", timeout=10)
    assert health.status_code == 200, f"stack not ready at {URL}: {health.text}"
    yield Stack()


def test_walking_skeleton(stack: Stack, capsys: pytest.CaptureFixture[str]) -> None:
    project = stack.project
    alice, bob, carol = stack.users["alice"], stack.users["bob"], stack.users["carol"]
    known: set[str] = set()

    # snapshot 1: CLI upload, then extraction -> TLR -> detection
    first = stack.ingest("s1", "s1", capsys)
    assert len(first["createdVersionIds"]) == 6
    w1 = stack.wait_workflow(known)
    assert [(s["agent"], s["skill"], s["status"]) for s in w1["steps"]] == [
        ("extraction-agent", "extract-artifact-versions", "SUCCEEDED"),
        ("tlr-agent", "recover-trace-links", "SUCCEEDED"),
        ("inconsistency-agent", "detect-inconsistencies", "SUCCEEDED"),
    ]

    requirements = stack.rows("MATCH (r:Requirement {projectId: $projectId}) RETURN r.name ORDER BY r.name")
    assert [r[0] for r in requirements] == ["Cache media files", "Encrypt user passwords", "Upload audio files"]
    assert stack.rows("MATCH (c:CodeUnit {projectId: $projectId, name: 'MediaCache'}) RETURN c.name") == [
        ["MediaCache"]
    ]
    tools = {r[0] for r in stack.rows("MATCH (r:Run {projectId: $projectId}) RETURN r.tool")}
    assert {"code-extractor", "stub-name-match", "stub-untraced,detect-stub"} <= tools

    links = stack.rows(
        "MATCH (t:TraceLink {projectId: $projectId, active: true})-[:SOURCE]->(s), (t)-[:TARGET]->(g) "
        "RETURN t.approach, s.key, g.name"
    )
    by_approach = {(approach, key.rsplit("#", 1)[1], target) for approach, key, target in links}
    assert ("explicit-reference", "REQ-1", "MediaCacheTest") in by_approach
    assert ("stub-name-match", "REQ-1", "MediaCache") in by_approach

    open_now = stack.inconsistencies()
    assert set(open_now) == {"Upload audio files", "Encrypt user passwords"}
    assert {i["status"] for i in open_now.values()} == {"OPEN"}
    assert {i["type"] for i in open_now.values()} == {"untraced-requirement"}
    req2, req3 = open_now["Upload audio files"], open_now["Encrypt user passwords"]

    # triage and authorization
    transition = {"to": "DEFERRED", "expectedStatus": "OPEN", "reason": "later"}
    assert carol.post(f"/v1/inconsistencies/{req3['id']}/transitions", json=transition).status_code == 403
    stale = {"to": "REJECTED", "expectedStatus": "DEFERRED", "reason": "stale"}
    assert bob.post(f"/v1/inconsistencies/{req3['id']}/transitions", json=stale).status_code == 409
    done = bob.post(f"/v1/inconsistencies/{req3['id']}/transitions", json=transition)
    assert done.status_code == 200 and done.json()["status"] == "DEFERRED", done.text

    # read isolation
    anchored = "MATCH (r:Requirement {projectId: $projectId}) RETURN count(r)"
    assert stack.cypher("carol", anchored).json()["rows"] == [[3]]
    assert stack.cypher("carol", "MATCH (r:Requirement) RETURN r").status_code == 422
    other = carol.post("/v1/query/cypher", json={"projectId": "unknown-project", "cypher": anchored})
    assert other.status_code in (403, 404)

    # exports
    csv = alice.get(f"/v1/projects/{project}/export/tracelinks?format=csv")
    assert csv.status_code == 200 and "explicit-reference" in csv.text and "stub-name-match" in csv.text

    # snapshot 2: one changed file, one deleted; re-sending changes nothing; REQ-2 now matches MediaCache
    second = stack.ingest("s2", "s2", capsys)
    assert len(second["createdVersionIds"]) == 1 and len(second["deletedArtifactIds"]) == 1
    stack.wait_workflow(known)
    again = stack.ingest("s2", "s2-again", capsys)
    assert (
        again["createdVersionIds"] == [] and again["reactivatedVersionIds"] == [] and again["deletedArtifactIds"] == []
    )
    time.sleep(15)
    assert {w["id"] for w in stack.workflows()} == known, "unchanged re-ingestion must not trigger a workflow"

    resolved = alice.get(f"/v1/inconsistencies/{req2['id']}").json()
    assert resolved["status"] == "RESOLVED" and resolved["verification"] == "confirmed", resolved
    assert any(h.get("actor") == "system:stub-untraced" for h in resolved["history"]), resolved["history"]
    deferred = alice.get(f"/v1/inconsistencies/{req3['id']}").json()
    assert deferred["status"] == "DEFERRED"

    # revert to snapshot 1: deleted/changed versions are reactivated, REQ-2 untraced again
    revert = stack.ingest("s1", "s1b", capsys)
    assert revert["createdVersionIds"] == [] and len(revert["reactivatedVersionIds"]) == 2
    stack.wait_workflow(known)
    reopened = alice.get(f"/v1/inconsistencies/{req2['id']}").json()
    assert reopened["status"] == "REOPENED", reopened
    assert alice.get(f"/v1/inconsistencies/{req3['id']}").json()["status"] == "DEFERRED"
