import hashlib
import io
import json
import tarfile
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path
from typing import Any

import httpx
import pytest
from sandman_cli.__main__ import build_manifest, kind_of, kind_rule, main, scan

MINI = Path(__file__).resolve().parents[3] / "tests" / "e2e" / "fixtures" / "mini"
RULES = [
    kind_rule(r)
    for r in (
        "requirements/**=requirements",
        "docs/**=architecture-doc",
        "model/**=architecture-model",
        "src/test/**=test",
        "src/main/**=code",
    )
]
WF = "wf_" + "1" * 32


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDMAN_TOKEN", "alice-token")
    monkeypatch.setenv("SANDMAN_URL", "http://qs:8080")


def test_kind_rule_matching() -> None:
    assert kind_of("requirements/requirements.md", RULES) == "requirements"
    assert kind_of("docs/sub/a.md", RULES) == "architecture-doc"
    assert kind_of("src/test/java/mini/MediaCacheTest.java", RULES) == "test"
    assert kind_of("src/main/java/mini/MediaCache.java", RULES) == "code"
    assert kind_of("README.md", RULES) == "other"
    assert kind_of("src/a.java", [kind_rule("src/*.java=code"), kind_rule("**=test")]) == "code"  # first match wins
    assert kind_of("src/x/a.java", [kind_rule("src/*.java=code")]) == "other"  # '*' stays in one segment
    with pytest.raises(Exception):  # noqa: B017 - argparse type error
        kind_rule("src/**=binary")


def test_manifest_of_mini_s1() -> None:
    files = scan(MINI / "s1")
    assert "sandman.yaml" not in files and len(files) == 6
    m = build_manifest(files, "full", "s1", RULES)
    assert m["mode"] == "full" and m["sourceRevision"] == "s1" and m["kindRules"] == RULES
    code = next(f for f in m["files"] if f["path"] == "src/main/java/mini/MediaCache.java")
    assert code["sha256"] == hashlib.sha256((MINI / "s1" / code["path"]).read_bytes()).hexdigest()


def server(
    requests: list[httpx.Request], missing: list[str], routes: dict[str, Any] | None = None
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer alice-token"
        if request.url.path.endswith("/ingestions/check"):
            return httpx.Response(200, json={"missing": missing})
        if request.url.path.endswith("/ingestions"):
            return httpx.Response(201, json={"ingestionId": "ing_" + "0" * 32, "seq": 1})
        return (routes or {})[request.url.path](request)

    return httpx.MockTransport(handle)


def multipart(request: httpx.Request) -> dict[str, bytes]:
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    msg = BytesParser(policy=HTTP).parsebytes(head + request.content)
    return {
        part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in msg.iter_parts()
    }  # type: ignore[attr-defined]


def test_ingest_uploads_only_missing_files(capsys: pytest.CaptureFixture[str]) -> None:
    s2 = MINI / "s2"
    req = s2 / "requirements" / "requirements.md"
    requests: list[httpx.Request] = []
    argv = ["ingest", "--project", "mini", "--mode", "full", "--revision", "s2", str(s2)]
    assert main(argv, server(requests, [hashlib.sha256(req.read_bytes()).hexdigest()])) == 0

    check, upload = requests
    assert (check.method, check.url.path) == ("POST", "/v1/projects/mini/ingestions/check")
    manifest = json.loads(check.content)
    assert len(manifest["files"]) == 5 and manifest["kindRules"] == RULES  # rules from s2/sandman.yaml
    assert upload.url.path == "/v1/projects/mini/ingestions"
    parts = multipart(upload)
    assert json.loads(parts["manifest"]) == manifest
    with tarfile.open(fileobj=io.BytesIO(parts["archive"]), mode="r:gz") as tar:
        assert tar.getnames() == ["requirements/requirements.md"]
        assert tar.extractfile("requirements/requirements.md").read() == req.read_bytes()  # type: ignore[union-attr]
    assert json.loads(capsys.readouterr().out)["seq"] == 1


def test_ingest_without_missing_files_sends_manifest_only(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x")
    token = tmp_path / "token"
    token.write_text("alice-token\n")
    requests: list[httpx.Request] = []
    argv = [
        "ingest",
        "--project",
        "mini",
        "--mode",
        "partial",
        "--kind-rule",
        "*.md=architecture-doc",
        "--token-file",
        str(token),
        str(tmp_path),
    ]
    assert main(argv, server(requests, [])) == 0
    parts = multipart(requests[1])
    assert set(parts) == {"manifest"}
    assert json.loads(parts["manifest"])["kindRules"] == [{"glob": "*.md", "kind": "architecture-doc"}]


def test_workflows_wait_polls_until_done(capsys: pytest.CaptureFixture[str]) -> None:
    states = iter(["RUNNING", "SUCCEEDED"])
    routes = {f"/v1/workflows/{WF}": lambda r: httpx.Response(200, json={"id": WF, "status": next(states)})}
    requests: list[httpx.Request] = []
    assert main(["workflows", "--project", "mini", "--wait", WF, "--poll", "0"], server(requests, [], routes)) == 0
    assert len(requests) == 2 and json.loads(capsys.readouterr().out)["status"] == "SUCCEEDED"


def test_workflows_list_and_export(capsys: pytest.CaptureFixture[str]) -> None:
    wf = {"id": WF, "status": "RUNNING", "trigger": {"kind": "event"}, "createdAt": "2026-10-07T00:00:00Z"}
    routes = {
        "/v1/projects/mini/workflows": lambda r: httpx.Response(200, json={"items": [wf]}),
        "/v1/projects/mini/export/tracelinks": lambda r: httpx.Response(200, text=f"format={r.url.params['format']}\n"),
    }
    requests: list[httpx.Request] = []
    assert main(["workflows", "--project", "mini"], server(requests, [], routes)) == 0
    assert main(["export", "--project", "mini", "tracelinks", "--format", "csv"], server(requests, [], routes)) == 0
    assert capsys.readouterr().out == f"{WF}\tRUNNING\tevent\t2026-10-07T00:00:00Z\nformat=csv\n"


def test_http_error_exits_nonzero() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(403, json={"title": "Forbidden"}))
    with pytest.raises(SystemExit, match="403"):
        main(["export", "--project", "mini", "inconsistencies"], transport)


def test_scan_skips_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.md").write_text("x")
    (root / "link.txt").symlink_to(outside)
    assert list(scan(root)) == ["a.md"]
