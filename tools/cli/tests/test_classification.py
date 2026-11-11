import builtins
import io
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from sandman_cli import classification
from sandman_cli.__main__ import main
from sandman_cli.classification import load_project

TOKEN = {"SANDMAN_TOKEN": "t", "SANDMAN_URL": "http://qs:8080"}


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in TOKEN.items():
        monkeypatch.setenv(k, v)


def project(tmp_path: Path, extra: dict[str, str] | None = None) -> Path:
    files = {
        "src/main/A.java": "class A {}",
        "src/main/B.java": "class B {}",
        "docs/overview.md": "# Overview",
        "docs/api.md": "# Api",
        "tools/gen/g.java": "class G {}",
        "tools/README.md": "# tools",
        "scripts/run.bin": "x",
        **(extra or {}),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, newline="")
    return tmp_path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(["classify", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_folded_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, str(project(tmp_path)))
    lines = out.splitlines()
    assert code == 0 and "7 files" in lines[0] and "classifier v1" in lines[0]
    assert any(ln.split()[:3] == ["code", "src/", "2"] for ln in lines)
    assert any(ln.split()[:3] == ["documentation", "?", "docs/"] for ln in lines)
    assert any(ln.split()[:2] == ["mixed", "tools/"] and "2 kinds" in ln for ln in lines)
    assert any("tools/gen/" in ln for ln in lines) and not any("src/main/A.java" in ln for ln in lines)
    assert "Review: not done" in out
    _, opened, _ = run(capsys, str(tmp_path), "--open", "src/main/")
    assert "src/main/A.java" in opened


def test_files_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    _, out, _ = run(capsys, str(root), "--files")
    assert "docs/api.md\tdocumentation\t-\theuristic\t" in out
    _, out, _ = run(capsys, str(root), "--json", "--all")
    doc = json.loads(out)
    assert set(doc) == {
        "root",
        "classifierVersion",
        "fileCount",
        "kinds",
        "ignored",
        "review",
        "rows",
        "files",
        "warnings",
    }
    assert doc["fileCount"] == 7 and doc["review"]["weak"] == 3 and doc["review"]["reviewed"] is False
    assert {r["path"] for r in doc["rows"]} >= {"src/main/A.java", "docs/api.md"}
    assert set(doc["files"][0]) == {"path", "kind", "format", "source", "reason", "weak"}


def test_rules_stdin_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"edits": [{"path": "docs", "kind": "requirements"}]})))
    code, out, _ = run(capsys, str(root), "--rules-stdin")
    res = json.loads(out)
    assert code == 0 and res["written"] is False and not (root / "sandman.yaml").exists()
    assert res["rules"] == [{"glob": "docs/**", "kind": "requirements"}]
    assert "reviewedRules:" in res["block"] and res["fileText"] == res["block"]

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([{"path": "docs/api.md", "kind": "other"}])))
    run(capsys, str(root), "--rules-stdin", "--write")
    proj = load_project(root, [])
    kinds = {p: v.kind for p, v in proj.verdicts().items()}
    assert kinds["docs/api.md"] == "other" and kinds["docs/overview.md"] == "documentation"
    assert [r["glob"] for r in proj.reviewed] == ["docs/api.md"]
    code, out, _ = run(capsys, str(root), "--check")
    assert code == 0 and "were not reviewed" not in out


def test_write_keeps_crlf_and_outside_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    original = '# mine\r\nkindRules:\r\n  - { glob: "scripts/**", kind: ignored }\r\n'
    (root / "sandman.yaml").write_bytes(original.encode())
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([{"path": "docs", "kind": "requirements"}])))
    run(capsys, str(root), "--rules-stdin", "--write")
    data = (root / "sandman.yaml").read_bytes()
    assert data.startswith(original.encode()) and b"\n" not in data.replace(b"\r\n", b"")
    assert not list(root.glob(".sandman.yaml.*"))
    _, out, _ = run(capsys, str(root), "--json")
    assert json.loads(out)["review"]["weak"] == 0 and json.loads(out)["ignored"] == {
        "scripts/run.bin": "rule scripts/**"
    }


def test_edited_block_needs_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = project(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([{"path": "docs", "kind": "requirements"}])))
    run(capsys, str(root), "--rules-stdin", "--write")
    cfg = root / "sandman.yaml"
    cfg.write_text(cfg.read_text().replace("requirements", "test"))
    edit = json.dumps([{"path": "docs/api.md", "kind": "other"}])
    monkeypatch.setattr("sys.stdin", io.StringIO(edit))
    with pytest.raises(SystemExit, match="edited by hand"):
        main(["classify", str(root), "--rules-stdin", "--write"])
    monkeypatch.setattr("sys.stdin", io.StringIO(edit))
    run(capsys, str(root), "--rules-stdin", "--write", "--force")
    assert "kind: test" not in cfg.read_text() and "docs/api.md" in cfg.read_text()


def test_check_exit_code_and_pin_legacy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    code, out, _ = run(capsys, str(root), "--check")
    assert code == 1 and "were not reviewed" in out and "kind changed in classifier v1: docs/api.md" in out
    assert run(capsys, str(root), "--pin-legacy", "--force")[0] == 0
    cfg = (root / "sandman.yaml").read_text()
    assert '"docs/api.md"' in cfg or '"docs/**"' in cfg
    proj = load_project(root, [])
    assert proj.verdicts()["docs/api.md"].kind != "documentation"


def test_ignore_precedence_and_shadow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, {"node_modules/x/i.js": "1", "vendor/v.go": "1", "keep/node_modules/k.js": "1"})
    (root / "sandman.yaml").write_text('kindRules:\n  - { glob: "docs/**", kind: test }\n')
    proj = load_project(root, [])
    vs = proj.verdicts()
    assert vs["node_modules/x/i.js"].kind == "ignored" and vs["vendor/v.go"].kind == "ignored"
    assert load_project(root, [{"glob": "vendor/**", "kind": "code"}]).verdicts()["vendor/v.go"].kind == "code"
    assert load_project(root, [{"glob": "docs/**", "kind": "other"}]).verdicts()["docs/api.md"].kind == "other"
    assert vs["docs/api.md"].kind == "test"
    block = classification.render_block([{"glob": "docs/**", "kind": "requirements"}], "x")
    (root / "sandman.yaml").write_text(
        '(kindRules:\n  - { glob: "docs/**", kind: test }\n'.replace("(", "") + "\n" + block
    )
    proj = load_project(root, [])
    assert proj.verdicts()["docs/api.md"].kind == "test"
    assert "shadows generated rule" in classification.shadow_warnings(proj, proj.verdicts())[0]
    _, out, _ = run(capsys, str(root))
    assert "ignored (node_modules/, vendor/)" in out.splitlines()[0] and "node_modules/x" not in out


def test_gitignore_respected(tmp_path: Path) -> None:
    root = project(tmp_path, {"gen/out.txt": "1", ".gitignore": "gen/\n"})
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    vs = load_project(root, []).verdicts()
    assert vs["gen/out.txt"].kind == "ignored" and vs["gen/out.txt"].reason == "ignored: .gitignore"
    assert vs["docs/api.md"].kind == "documentation"
    assert load_project(root, [{"glob": "gen/**", "kind": "code"}]).verdicts()["gen/out.txt"].kind == "code"


def server(requests: list[httpx.Request], kinds: dict[str, int] | None = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/check"):
            return httpx.Response(200, json={"missing": []})
        body: dict[str, object] = {"ingestionId": "ing_" + "0" * 32, "seq": 1}
        if kinds is not None:
            body.update(kinds=kinds, warnings=["classifierVersion 0 differs"])
        return httpx.Response(201, json=body)

    return httpx.MockTransport(handle)


def ingest(root: Path, requests: list[httpx.Request], *extra: str, kinds: dict[str, int] | None = None) -> int:
    argv = ["ingest", "--project", "mini", "--mode", "full", *extra, str(root)]
    return main(argv, server(requests, kinds))


def tty(monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
    monkeypatch.setattr(classification, "is_tty", lambda: True)
    it = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda _="": next(it))


def test_ingest_skips_ignored_and_sends_version(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, {"node_modules/x/i.js": "1"})
    requests: list[httpx.Request] = []
    assert ingest(root, requests, "--no-review") == 0
    manifest = json.loads(requests[0].content)
    assert manifest["classifierVersion"] == 1
    assert "node_modules/x/i.js" not in {f["path"] for f in manifest["files"]} and len(manifest["files"]) == 7
    assert "ignored (node_modules/)" in capsys.readouterr().err


def test_ingest_review_no_tty_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    requests: list[httpx.Request] = []
    assert ingest(project(tmp_path), requests) == 2
    assert requests == [] and "3 files in 2 folders were not reviewed" in capsys.readouterr().err


def test_ingest_review_choices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    requests: list[httpx.Request] = []
    tty(monkeypatch, "a")
    assert ingest(root, requests) == 1 and requests == []
    tty(monkeypatch, "x", "p")
    assert ingest(root, requests) == 0 and len(requests) == 2
    requests.clear()
    tty(monkeypatch, "r", "k", "k", "y")
    assert ingest(root, requests) == 0 and len(requests) == 2
    assert load_project(root, []).state.reviewed == classification.weak_fingerprint(
        classification.weak_paths(load_project(root, []).verdicts())
    )
    requests.clear()
    monkeypatch.setattr(builtins, "input", lambda _="": pytest.fail("asked although reviewed"))
    assert ingest(root, requests) == 0 and len(requests) == 2


def test_ingest_no_weak_does_not_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    (root / "sandman.yaml").write_text('kindRules:\n  - { glob: "**", kind: code }\n')
    requests: list[httpx.Request] = []
    monkeypatch.setattr(builtins, "input", lambda _="": pytest.fail("asked"))
    assert ingest(root, requests) == 0


def test_ingest_review_changes_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    requests: list[httpx.Request] = []
    tty(monkeypatch, "c", "requirements", "k", "y")
    assert ingest(root, requests, "--review") == 0
    manifest = json.loads(requests[0].content)
    assert {"glob": "docs/**", "kind": "requirements"} in manifest["kindRules"]


def test_kinds_mismatch_and_server_warnings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    assert ingest(root, [], "--no-review", kinds={"code": 99, "other": 1}) == 0
    err = capsys.readouterr().err
    assert "server kinds differ from the preview" in err and "code 3 -> 99" in err
    assert "Warning: classifierVersion 0 differs" in err
