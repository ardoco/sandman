import json
import os
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "actions" / "sandman-ingest" / "run.sh"
MINI = ROOT / "tests" / "e2e" / "fixtures" / "mini" / "s1"
TOKEN = "sdm_secret_value"
WF = "wf_" + "a" * 32
INGESTION = {
    "ingestionId": "ing_1",
    "projectId": "demo",
    "seq": 1,
    "mode": "full",
    "createdVersionIds": ["v1"],
    "reactivatedVersionIds": [],
    "deletedArtifactIds": [],
    "unchangedCount": 0,
    "createdAt": "2026-10-18T10:00:00Z",
}


class Mock(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, str | None, bytes]]
    behavior: dict[str, Any]

    def _reply(self, code: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).requests.append((self.command, self.path, self.headers.get("Authorization"), body))
        b = type(self).behavior
        if b.get("ingest_status") and self.path.endswith("/ingestions"):
            return self._reply(b["ingest_status"], {"detail": "forbidden"})
        if self.path.endswith("/ingestions/check"):
            return self._reply(200, {"missing": [f["sha256"] for f in json.loads(body)["files"]]})
        if self.path.endswith("/ingestions"):
            return self._reply(200, b.get("ingestion", INGESTION))
        if self.path.endswith("/workflows"):
            return self._reply(
                200,
                {
                    "items": b.get(
                        "items",
                        [
                            {
                                "id": WF,
                                "status": "RUNNING",
                                "trigger": {"kind": "event"},
                                "createdAt": "2026-10-18T10:00:02Z",
                            }
                        ],
                    )
                },
            )
        if self.path == f"/v1/workflows/{WF}":
            return self._reply(200, {"id": WF, "status": b.get("final", "SUCCEEDED"), "steps": []})
        self._reply(404, {})

    do_GET = do_POST = _handle

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def server() -> Iterator[HTTPServer]:
    Mock.requests, Mock.behavior = [], {}
    srv = HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def run(server: HTTPServer, tmp_path: Path, **extra: str) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    out = tmp_path / "output"
    env = {
        **os.environ,
        "SANDMAN_URL": f"http://127.0.0.1:{server.server_port}",
        "SANDMAN_TOKEN": TOKEN,
        "SANDMAN_PROJECT": "demo",
        "SANDMAN_CMD": "uv run --package sandman-cli sandman",
        "SANDMAN_POLL": "0.1",
        "INPUT_PATH": str(MINI),
        "INPUT_MODE": "full",
        "INPUT_REVISION": "abc123",
        "GITHUB_OUTPUT": str(out),
        **extra,
    }
    proc = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True, cwd=ROOT, check=False)
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines()) if out.exists() else {}
    return proc, outputs


def test_ingest_waits_and_sets_outputs(server: HTTPServer, tmp_path: Path) -> None:
    proc, outputs = run(server, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert outputs == {"ingestion-id": "ing_1", "workflow-id": WF, "status": "SUCCEEDED"}
    assert all(auth == f"Bearer {TOKEN}" for _, _, auth, _ in Mock.requests)
    ingest = next(r for r in Mock.requests if r[0] == "POST" and r[1] == "/v1/projects/demo/ingestions")
    assert b'"sourceRevision": "abc123"' in ingest[3] and b'"mode": "full"' in ingest[3]
    assert f"::add-mask::{TOKEN}" in proc.stdout
    assert TOKEN not in proc.stdout.replace(f"::add-mask::{TOKEN}", "") + proc.stderr


def test_no_wait_reports_started(server: HTTPServer, tmp_path: Path) -> None:
    proc, outputs = run(server, tmp_path, INPUT_WAIT="false")
    assert proc.returncode == 0, proc.stderr
    assert outputs["status"] == "STARTED" and outputs["workflow-id"] == WF
    assert not any(p.startswith("/v1/workflows/") for _, p, _, _ in Mock.requests)


def test_unchanged_ingestion_starts_no_workflow(server: HTTPServer, tmp_path: Path) -> None:
    Mock.behavior["ingestion"] = {**INGESTION, "createdVersionIds": [], "unchangedCount": 6}
    proc, outputs = run(server, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert outputs == {"ingestion-id": "ing_1", "workflow-id": "", "status": "none"}


def test_failed_workflow_fails_step_with_status(server: HTTPServer, tmp_path: Path) -> None:
    Mock.behavior["final"] = "FAILED"
    proc, outputs = run(server, tmp_path)
    assert proc.returncode == 1
    assert outputs["status"] == "FAILED"


def test_http_error_fails_with_status_code(server: HTTPServer, tmp_path: Path) -> None:
    Mock.behavior["ingest_status"] = 403
    proc, outputs = run(server, tmp_path)
    assert proc.returncode != 0 and outputs == {}
    assert "403" in proc.stderr and TOKEN not in proc.stdout.replace(f"::add-mask::{TOKEN}", "") + proc.stderr


def test_empty_token_fails_before_any_request(server: HTTPServer, tmp_path: Path) -> None:
    proc, _ = run(server, tmp_path, SANDMAN_TOKEN="")
    assert proc.returncode == 1 and "::error::input token is empty" in proc.stdout
    assert Mock.requests == []


def test_partial_uploads_only_changed_files(server: HTTPServer, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "a.md").write_text("# A\n")
    (repo / "docs" / "b.md").write_text("# B\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo)]
    for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "one"]):
        subprocess.run([*git, *cmd], check=True)
    (repo / "docs" / "b.md").write_text("# B2\n")
    for cmd in (["add", "."], ["commit", "-qm", "two"]):
        subprocess.run([*git, *cmd], check=True)
    proc, _ = run(server, tmp_path, INPUT_PATH=str(repo), INPUT_MODE="partial", INPUT_BASE="HEAD~1")
    assert proc.returncode == 0, proc.stderr
    check = next(r for r in Mock.requests if r[1].endswith("/check"))
    assert [f["path"] for f in json.loads(check[3])["files"]] == ["docs/b.md"]
