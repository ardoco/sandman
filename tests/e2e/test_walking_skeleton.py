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
import shutil
import subprocess
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
COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(ROOT / "deploy" / "docker-compose.yml"),
    "-f",
    str(ROOT / "deploy" / "docker-compose.dev.yml"),
    "--profile",
    "stubs",
    "--profile",
    "dashboard",
]
LOG_CAT = [*COMPOSE, "exec", "-T", "inconsistency-agent", "cat", "/logs/sandman.jsonl"]


def find_key(node: Any, key: str) -> Any:
    """First value stored under `key` anywhere in a nested JSON document (None if absent)."""
    if isinstance(node, dict):
        if key in node:
            return node[key]
        node = list(node.values())
    if isinstance(node, list):
        for child in node:
            if (found := find_key(child, key)) is not None:
                return found
    return None


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
    def __init__(self, project: str | None = None) -> None:
        suffix = project or secrets.token_hex(3)
        self.project = project or f"e2e-{suffix}"
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

    def notifications(self) -> list[dict[str, Any]]:
        """Notification lines of this project from the inconsistency-agent's JSONL log (SANDMAN_LOG_FILE, enabled by
        `make e2e`); fails when the log is not readable."""
        run = subprocess.run(LOG_CAT, capture_output=True, text=True, check=False)
        if run.returncode:
            pytest.fail(
                f"log not readable in the container ({run.stderr.strip()}); run via make e2e (SANDMAN_LOG_FILE)"
            )
        lines = run.stdout.splitlines()
        events = []
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (
                isinstance(event, dict)
                and event.get("event") == "notification"
                and event.get("projectId") == self.project
            ):
                events.append(event)
        return events

    def ingest(self, snapshot: str | Path, revision: str, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
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

    def a2a_result(self, agent: str, task_id: str) -> dict[str, Any]:
        """Output of an A2A task. The workflow step stores only the completion event, and A2A is internal, so the
        orchestrator container asks the agent with its own client."""
        code = (
            "import asyncio,json,sys\n"
            "from sandman_agent.a2a import A2aClient\n"
            "async def main():\n"
            "    a = A2aClient(open('/run/secrets/svc_token_orchestrator').read())\n"
            "    print(json.dumps((await a.get(sys.argv[1], sys.argv[2])).result))\n"
            "asyncio.run(main())\n"
        )
        port = 8000 if agent == "extraction-agent" else 8080
        cmd = [*COMPOSE, "exec", "-T", "orchestrator", "python", "-c", code, f"http://{agent}:{port}", task_id]
        run = subprocess.run(cmd, capture_output=True, text=True, check=True)
        result: dict[str, Any] = json.loads(run.stdout.splitlines()[-1])
        return result

    def cypher(self, who: str, text: str, **extra: Any) -> httpx.Response:
        return self.users[who].post("/v1/query/cypher", json={"projectId": self.project, "cypher": text, **extra})

    def rows(self, text: str) -> list[list[Any]]:
        resp = self.cypher("alice", text)
        assert resp.status_code == 200, resp.text
        rows: list[list[Any]] = resp.json()["rows"]
        return rows

    def explicit_link_count(self) -> int:
        """Active explicit-reference links REQ-1 -> MediaCacheTest."""
        rows = self.rows(
            "MATCH (t:TraceLink {projectId: $projectId, active: true, approach: 'explicit-reference'})-[:SOURCE]->(s), "
            "(t)-[:TARGET]->(g {name: 'MediaCacheTest'}) WHERE s.key ENDS WITH '#REQ-1' RETURN count(t)"
        )
        return int(rows[0][0])

    def inconsistencies(self) -> dict[str, dict[str, Any]]:
        items = self.users["alice"].get(f"/v1/projects/{self.project}/inconsistencies").json()["items"]
        return {i["title"].rsplit(": ", 1)[1]: i for i in items if i["type"] == "untraced-requirement"}

    def ardoco_findings(self) -> dict[str, tuple[str, str]]:
        items = self.users["alice"].get(f"/v1/projects/{self.project}/inconsistencies?limit=200").json()["items"]
        return {i["id"]: (i["type"], i["status"]) for i in items if i["type"] != "untraced-requirement"}


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    health = httpx.get(f"{URL}/readyz", timeout=10)
    assert health.status_code == 200, f"stack not ready at {URL}: {health.text}"
    yield Stack()


def test_walking_skeleton(stack: Stack, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
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
    assert [r[0] for r in requirements] == ["Cache media files", "Encrypt user passwords", "Print monthly invoices"]
    assert stack.rows("MATCH (c:CodeUnit {projectId: $projectId, name: 'MediaCache'}) RETURN c.name") == [
        ["MediaCache"]
    ]
    tools = {r[0] for r in stack.rows("MATCH (r:Run {projectId: $projectId}) RETURN r.tool")}
    assert {"code-extractor", "stub-name-match"} <= tools
    assert any("ardoco-id" in t and "stub-untraced" in t and "detect-stub" in t for t in tools), tools

    # the plugin stub's warning is passed through to detectorRuns[].warnings (R-021)
    detect = stack.a2a_result("inconsistency-agent", w1["steps"][2]["a2aTaskId"])["detectorRuns"]
    ardoco_run = next(run for run in detect if run["detector"] == "ardoco-id")
    assert ardoco_run["status"] == "completed", ardoco_run
    warnings = [w for run in detect for w in run.get("warnings", [])]
    assert any(w.startswith(f"plugin-stubs: project {project} has ") for w in warnings), detect

    links = stack.rows(
        "MATCH (t:TraceLink {projectId: $projectId, active: true})-[:SOURCE]->(s), (t)-[:TARGET]->(g) "
        "RETURN t.approach, s.key, g.name"
    )
    by_approach = {(approach, key.rsplit("#", 1)[1], target) for approach, key, target in links}
    assert ("explicit-reference", "REQ-1", "MediaCacheTest") in by_approach
    assert ("stub-name-match", "REQ-1", "MediaCache") in by_approach

    open_now = stack.inconsistencies()
    assert set(open_now) == {"Print monthly invoices", "Encrypt user passwords"}
    assert {i["status"] for i in open_now.values()} == {"OPEN"}
    assert {i["type"] for i in open_now.values()} == {"untraced-requirement"}
    req2, req3 = open_now["Print monthly invoices"], open_now["Encrypt user passwords"]

    # one notification line per new inconsistency
    opened = {
        e["title"].rsplit(": ", 1)[1]
        for e in stack.notifications()
        if e["status"] == "OPEN" and e["type"] == "untraced-requirement"
    }
    assert opened == {"Print monthly invoices", "Encrypt user passwords"}, opened

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
    assert any(e["status"] == "REOPENED" and e["inconsistencyId"] == req2["id"] for e in stack.notifications())

    # manual trigger (R-021): POST /workflows {type: tlr, force} runs TLR steps only (one per kind pair)
    manual = alice.post(f"/v1/projects/{project}/workflows", json={"type": "tlr", "force": True})
    assert manual.status_code == 202, manual.text
    done_manual = stack.wait_workflow(known)
    assert done_manual["id"] == manual.json()["id"]
    assert {(s["agent"], s["skill"], s["status"]) for s in done_manual["steps"]} == {
        ("tlr-agent", "recover-trace-links", "SUCCEEDED")
    }
    assert stack.explicit_link_count() == 1

    # T1.12 scope 4: only the requirements file changes; REQ-1 -> MediaCacheTest is current again afterwards
    changed = tmp_path / "s3"
    shutil.copytree(MINI / "s1", changed)
    file = changed / "requirements" / "requirements.md"
    # new version with unchanged element contents: enrichment is reused, no new LLM call
    file.write_text(file.read_text() + "\n")
    third = stack.ingest(changed, "s3", capsys)
    assert len(third["createdVersionIds"]) == 1 and third["deletedArtifactIds"] == [], third
    stack.wait_workflow(known)
    assert stack.explicit_link_count() == 1
    current_req1 = stack.rows(
        "MATCH (:Artifact {projectId: $projectId})-[:CURRENT_VERSION]->(:ArtifactVersion)"
        "-[:HAS_ELEMENT]->(r:Requirement) WHERE r.key ENDS WITH '#REQ-1' RETURN r.id"
    )
    link_source = stack.rows(
        "MATCH (t:TraceLink {projectId: $projectId, active: true, approach: 'explicit-reference'})-[:SOURCE]->(s), "
        "(t)-[:TARGET]->(g {name: 'MediaCacheTest'}) WHERE s.key ENDS WITH '#REQ-1' RETURN s.id"
    )
    assert len(current_req1) == 1 and link_source == current_req1
    stale = stack.rows(
        "MATCH (t:TraceLink {projectId: $projectId, active: true, approach: 'explicit-reference'})"
        "-[:SOURCE|TARGET]->(e) OPTIONAL MATCH (a:Artifact)-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(e) "
        "WITH t, a WHERE a IS NULL RETURN count(t)"
    )
    assert stale == [[0]]
