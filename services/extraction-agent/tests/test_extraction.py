"""T0.5 acceptance tests (Testcontainers Memgraph + NATS)."""

import hashlib
import io
import json
import random
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
from sandman_agent import Contracts, PublishError, stable_id
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_extraction import ingest
from sandman_extraction.extract import APPROACH, APPROACH_VERSION, match_references
from starlette.exceptions import HTTPException

REQ = "requirements/requirements.md"
OLD = "docs/old-notes.md"
CODE = "src/main/java/mini/MediaCache.java"
TEST = "src/test/java/mini/MediaCacheTest.java"
INGESTION = "#/components/schemas/Ingestion"


def art(project: str, path: str) -> str:
    return stable_id("art", project, path)


def ver(project: str, path: str, data: bytes) -> str:
    return stable_id("ver", art(project, path), hashlib.sha256(data).hexdigest())


async def created(env: Any, files: dict[str, bytes], mode: str = "full") -> dict[str, Any]:
    resp = await env.ingest(files, mode)
    assert resp.status_code == 201, resp.text
    env.validate(INGESTION, resp.json())
    return resp.json()


async def cypher(env: Any, query: str, **params: Any) -> list[dict[str, Any]]:
    return await env.svc.kg.read(query, **params)


async def test_project_and_probes(env: Any) -> None:
    resp = await env.put_project()
    env.validate("#/components/schemas/Project", resp.json())
    assert resp.json()["id"] == env.project
    assert (await env.http.get(f"/v1/projects/{env.project}")).json()["name"] == "Mini"
    assert (await env.http.get("/v1/projects/nope-nope")).status_code == 404
    assert (await env.http.get("/healthz")).status_code == 200
    assert (await env.http.get("/readyz")).status_code == 200
    assert (await env.http.get("/.well-known/agent-card.json")).status_code == 200
    unauth = await env.http.get(f"/v1/projects/{env.project}", headers={"Authorization": "Bearer wrong"})
    assert unauth.status_code == 401
    status = await env.http.get("/v1/status")
    assert status.status_code == 200


async def test_two_full_ingestions(env: Any) -> None:
    await env.put_project()
    files = env.mini()
    first = await created(env, files)
    assert first["seq"] == 1 and first["ingestionId"] == stable_id("ing", env.project, "1")
    assert len(first["createdVersionIds"]) == len(files)
    changed = {p: d for p, d in files.items() if p != OLD} | {REQ: files[REQ] + b"\n## REQ-4 New\nText\n"}
    second = await created(env, changed)
    assert second["createdVersionIds"] == [ver(env.project, REQ, changed[REQ])]
    assert second["deletedArtifactIds"] == [art(env.project, OLD)]
    assert second["unchangedCount"] == len(files) - 2
    current = await cypher(
        env,
        "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v) RETURN a.path AS path, v.id AS v",
        p=env.project,
    )
    assert {r["path"]: r["v"] for r in current}[REQ] == ver(env.project, REQ, changed[REQ])
    assert OLD not in {r["path"] for r in current}
    deleted = await cypher(
        env, "MATCH (a:Artifact {projectId: $p}) WHERE a.deletedAt IS NOT NULL RETURN a.path AS path", p=env.project
    )
    assert [r["path"] for r in deleted] == [OLD]
    versions = await cypher(
        env, "MATCH (:Artifact {id: $a})-[:HAS_VERSION]->(v) RETURN count(v) AS n", a=art(env.project, REQ)
    )
    assert versions[0]["n"] == 2
    assert not await cypher(env, "MATCH ()-[r:PREVIOUS]->() RETURN r")
    events = await env.events("sandman.ingestion.completed")
    assert [e["data"]["seq"] for e in events] == [1, 2]
    assert events[1]["data"]["actor"] == "service:cli"
    got = await env.http.get(f"/v1/projects/{env.project}/ingestions/{second['ingestionId']}")
    assert got.json() == second


async def test_same_ingestion_twice_changes_nothing(env: Any) -> None:
    await env.put_project()
    await created(env, env.mini())
    again = await created(env, env.mini())
    assert again["seq"] == 2 and again["createdVersionIds"] == [] and again["reactivatedVersionIds"] == []
    assert again["deletedArtifactIds"] == [] and again["unchangedCount"] == len(env.mini())
    assert (await cypher(env, "MATCH (v:ArtifactVersion {projectId: $p}) RETURN count(v) AS n", p=env.project))[0][
        "n"
    ] == len(env.mini())
    assert len(await env.events("sandman.ingestion.completed")) == 1


async def test_publish_failure_then_retry_publishes_once(env: Any) -> None:
    await env.put_project()
    real = env.svc.events

    class Down:
        async def publish(self, *args: Any, **kwargs: Any) -> Any:
            raise PublishError("down")

    env.svc.events = Down()
    failed = await env.ingest(env.mini())
    assert failed.status_code == 503
    env.validate("shared:#/components/schemas/Problem", failed.json())
    assert (await cypher(env, "MATCH (g:Ingestion {projectId: $p}) RETURN g.eventPublished AS ok", p=env.project)) == [
        {"ok": False}
    ]
    env.svc.events = real
    retry = await created(env, env.mini())
    assert retry["seq"] == 2 and retry["createdVersionIds"] == []
    events = await env.events("sandman.ingestion.completed")
    assert len(events) == 1 and events[0]["data"]["seq"] == 1
    flags = await cypher(env, "MATCH (g:Ingestion {projectId: $p}) RETURN g.eventPublished AS ok", p=env.project)
    assert all(r["ok"] for r in flags)
    # startup re-publish of an unpublished ingestion is deduplicated by the deterministic event id
    await cypher_write(env, "MATCH (g:Ingestion {seq: 1, projectId: $p}) SET g.eventPublished = false")
    await ingest.republish_pending(env.svc.kg, env.svc)
    assert len(await env.events("sandman.ingestion.completed")) == 1


async def cypher_write(env: Any, query: str) -> None:
    await env.svc.kg.write(query, p=env.project)


async def test_revert_and_readd_reactivate(env: Any) -> None:
    await env.put_project()
    a = env.mini()
    await created(env, a)
    b = a | {REQ: a[REQ] + b"\nchanged\n"}
    await created(env, b)
    back = await created(env, a)
    assert back["createdVersionIds"] == [] and back["reactivatedVersionIds"] == [ver(env.project, REQ, a[REQ])]
    cur = await cypher(
        env, "MATCH (:Artifact {id: $a})-[:CURRENT_VERSION]->(v) RETURN v.id AS v", a=art(env.project, REQ)
    )
    assert cur == [{"v": ver(env.project, REQ, a[REQ])}]
    gone = {p: d for p, d in a.items() if p != OLD}
    await created(env, gone)
    readd = await created(env, a)
    assert readd["reactivatedVersionIds"] == [ver(env.project, OLD, a[OLD])] and readd["createdVersionIds"] == []
    nodes = await cypher(env, "MATCH (x:Artifact {id: $a}) RETURN x.deletedAt AS d", a=art(env.project, OLD))
    assert nodes == [{"d": None}]
    assert [e["data"]["seq"] for e in await env.events("sandman.ingestion.completed")] == [1, 2, 3, 4, 5]


async def test_archive_path_traversal_rejected(env: Any) -> None:
    await env.put_project()
    data = b"evil"
    manifest = {"mode": "full", "files": [{"path": "ok.txt", "sha256": hashlib.sha256(data).hexdigest()}]}
    for name in ("../ok.txt", "/ok.txt"):
        parts = {
            "manifest": (None, json.dumps(manifest), "application/json"),
            "archive": ("a.tar.gz", env.tar_gz({name: data}), "application/gzip"),
        }
        resp = await env.http.post(f"/v1/projects/{env.project}/ingestions", files=parts)
        assert resp.status_code in (400, 422), resp.text
        env.validate("shared:#/components/schemas/Problem", resp.json())
    bad_manifest = {"mode": "full", "files": [{"path": "../x", "sha256": "0" * 64}]}
    assert (await env.http.post(f"/v1/projects/{env.project}/ingestions/check", json=bad_manifest)).status_code == 400


async def extract_mini(env: Any) -> tuple[dict[str, bytes], dict[str, Any]]:
    await env.put_project()
    files = env.mini()
    ing = await created(env, files)
    task = await env.extract(ing["createdVersionIds"])
    assert task.state == "TASK_STATE_COMPLETED", task
    return files, task.result


async def test_extract_mini(env: Any) -> None:
    files, result = await extract_mini(env)
    Contracts(DEFAULT_CONTRACTS_DIR).validate("skills.schema.json#/$defs/ExtractArtifactVersionsOutput", result)
    assert result["status"] == "completed"
    reqs = await cypher(
        env, "MATCH (r:Requirement {projectId: $p}) RETURN r.name AS name, r.id AS id ORDER BY r.name", p=env.project
    )
    assert len(reqs) == 3
    v = ver(env.project, REQ, files[REQ])
    assert reqs[0]["id"] == stable_id("el", v, "REQ-1")
    link = await cypher(
        env,
        "MATCH (r:Requirement {id: $r})<-[:SOURCE]-(l:TraceLink)-[:TARGET]->(t:Element {name: 'MediaCacheTest'}) "
        "RETURN l.approach AS approach, l.confidence AS c, l.status AS s, l.linkType AS lt, l.active AS a, l.id AS id",
        r=reqs[0]["id"],
    )
    assert len(link) == 1
    assert (link[0]["approach"], link[0]["c"], link[0]["s"], link[0]["a"]) == (
        "explicit-reference",
        1.0,
        "candidate",
        True,
    )
    el_test = stable_id("el", ver(env.project, TEST, files[TEST]), "MediaCacheTest")
    cfg = env.svc.explicit_hash
    assert link[0]["id"] == stable_id("tl", "explicit-reference", "0", cfg, reqs[0]["id"], el_test)
    done = await env.events("sandman.extraction.completed")
    assert len(done) == 1 and done[0]["data"]["elementCount"] == result["elementCount"]


async def test_plugin_without_references_gets_the_configured_patterns_applied(env: Any) -> None:
    env.plugin.silent = True
    files, result = await extract_mini(env)
    assert result["status"] == "completed"
    req1 = stable_id("el", ver(env.project, REQ, files[REQ]), "REQ-1")
    rows = await cypher(
        env,
        "MATCH (:Requirement {id: $r})<-[:SOURCE]-(l:TraceLink)-[:TARGET]->(t:Element) "
        "WHERE l.approach = 'explicit-reference' RETURN t.kind AS kind, t.name AS name, l.linkType AS lt",
        r=req1,
    )
    assert rows == [{"kind": "type", "name": "MediaCacheTest", "lt": "verifies"}]  # the comment sits above the class


def test_match_references_with_a_file_element_without_lines() -> None:
    file = {"localKey": "f.java"}
    outer = {"localKey": "Outer", "startLine": 10, "endLine": 30}
    inner = {"localKey": "Outer.Inner", "startLine": 20, "endLine": 25}
    text = "// REQ-1\n" + "\n" * 11 + "REQ-2\n" + "\n" * 7 + "REQ-3\n" + "\n" * 10 + "REQ-4\n"
    refs = match_references(text.encode(), [file, outer, inner], [r"REQ-\d"], "implements")
    assert [(r["text"], r["source"], r["linkType"]) for r in refs] == [
        ("REQ-1", "Outer", "implements"),  # leading comment: next element with lines
        ("REQ-2", "Outer", "implements"),
        ("REQ-3", "Outer.Inner", "implements"),
        ("REQ-4", "f.java", "implements"),  # after the last element: the file
    ]
    assert match_references(b"REQ-1", [], [r"REQ-\d"]) == []


async def test_plugin_failure_falls_back(env: Any) -> None:
    env.plugin.fail = True
    files, result = await extract_mini(env)
    assert result["status"] == "partial"
    assert [r["status"] for r in result["pluginRuns"]] == ["failed"]
    code = stable_id("el", ver(env.project, CODE, files[CODE]), "file")
    rows = await cypher(env, "MATCH (e:CodeUnit {id: $id}) RETURN e.kind AS kind", id=code)
    assert rows == [{"kind": "file"}]


async def test_reextraction_drops_elements_and_is_idempotent(env: Any) -> None:
    env.plugin.extra = True
    files, first = await extract_mini(env)
    v = ver(env.project, CODE, files[CODE])
    count = "MATCH (:ArtifactVersion {id: $v})-[:HAS_ELEMENT]->(e) RETURN count(e) AS n"
    assert (await cypher(env, count, v=v))[0]["n"] == 3  # file, class, method
    snapshot = "MATCH (n) WHERE n:Element OR n:TraceLink RETURN count(n) AS n"
    before = (await cypher(env, snapshot))[0]["n"]
    env.plugin.extra = False
    versions = await cypher(
        env, "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v) RETURN v.id AS id", p=env.project
    )
    task = await env.extract([r["id"] for r in versions])
    assert task.state == "TASK_STATE_COMPLETED"
    assert (await cypher(env, count, v=v))[0]["n"] == 2
    assert (await cypher(env, snapshot))[0]["n"] == before  # no new facts, dropped element node remains unattached
    again = await env.extract([r["id"] for r in versions])
    assert (await cypher(env, snapshot))[0]["n"] == before
    assert again.result["elementCount"] == task.result["elementCount"]
    active = await cypher(env, "MATCH (l:TraceLink {projectId: $p, active: true}) RETURN count(l) AS n", p=env.project)
    assert active[0]["n"] == 1


async def test_intake_rejections_and_actor(env: Any) -> None:
    await env.put_project()
    data = b"x"
    manifest = {"mode": "full", "files": [{"path": "a.txt", "sha256": hashlib.sha256(data).hexdigest()}]}

    async def post(archive: bytes | None) -> Any:
        parts: dict[str, Any] = {"manifest": (None, json.dumps(manifest), "application/json")}
        if archive is not None:
            parts["archive"] = ("a", archive, "application/octet-stream")
        return await env.http.post(f"/v1/projects/{env.project}/ingestions", files=parts)

    assert (await post(None)).status_code == 422  # unknown hash, no content
    assert (await post(env.tar_gz({"a.txt": b"y"}))).status_code == 422  # hash mismatch
    link = io.BytesIO()
    with tarfile.open(fileobj=link, mode="w") as tf:
        info = tarfile.TarInfo("a.txt")
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tf.addfile(info)
    assert (await post(link.getvalue())).status_code == 400
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as zf:
        zf.writestr("../a.txt", data)
    assert (await post(zipped.getvalue())).status_code == 400
    with pytest.raises(HTTPException):
        ingest.read_archive(env.tar_gz({"a.txt": data}), max_bytes=0)
    # actor forwarded by the trusted service, rejected from others
    resp = await env.ingest(env.mini(), **{"Authorization": "Bearer query-token", "X-Sandman-Actor": "user:alice"})
    assert resp.status_code == 201
    assert (await env.events("sandman.ingestion.completed"))[0]["data"]["actor"] == "user:alice"


ARCH = "docs/architecture.md"
SENTENCES = (
    "MATCH (:ArtifactVersion {id: $v})-[:HAS_ELEMENT]->(e:TextUnit {kind: 'sentence'}) "
    "RETURN e.text AS text, e.sentenceIndex AS i ORDER BY e.sentenceIndex"
)


async def test_builtin_sections_sentences_and_reextraction(env: Any) -> None:
    files, result = await extract_mini(env)
    assert result["status"] == "completed"
    v = ver(env.project, ARCH, files[ARCH])
    assert await cypher(env, SENTENCES, v=v) == [
        {"text": "The MediaCache component caches media files.", "i": 0},
        {"text": "The UserDB component stores users.", "i": 1},
    ]
    chain = await cypher(
        env,
        "MATCH (:ArtifactVersion {id: $v})-[:HAS_ELEMENT]->(f:Element {kind: 'file'})-[:CONTAINS]->(s:TextUnit "
        "{kind: 'section'})-[:CONTAINS]->(t:TextUnit {kind: 'sentence'}) RETURN s.key AS sec, count(t) AS n",
        v=v,
    )
    assert [(r["sec"].split("#", 1)[1], r["n"]) for r in chain] == [("sec:architecture", 2)]
    run = await cypher(env, "MATCH (r:Run {id: $r}) RETURN r.tool AS tool", r=result["runId"])
    assert run == [{"tool": "builtin-extractor"}]

    changed = files[ARCH].replace(b" The UserDB component stores users.", b"") + b"\nA new sentence appears.\n"
    resp = await created(env, files | {ARCH: changed})
    again = await env.extract(resp["createdVersionIds"])
    assert again.state == "TASK_STATE_COMPLETED"
    cur = await cypher(
        env,
        "MATCH (:Artifact {id: $a})-[:CURRENT_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(e:TextUnit "
        "{kind: 'sentence'}) RETURN e.text AS text ORDER BY e.sentenceIndex",
        a=art(env.project, ARCH),
    )
    assert [r["text"] for r in cur] == ["The MediaCache component caches media files.", "A new sentence appears."]


async def test_requirement_references_direction_and_idempotence(env: Any) -> None:
    await env.put_project()
    csv_path, doc_path = "requirements/more.csv", "docs/extra.md"
    files = env.mini() | {
        csv_path: b"id,text\nREQ-7,Refines REQ-1 and mentions itself as REQ-7.\nREQ-8,Unrelated REQ-99.\n",
        doc_path: b"# Extra\n\nThe design realizes REQ-3 and REQ-8.\n",
    }
    ing = await created(env, files)
    task = await env.extract(ing["createdVersionIds"])
    assert task.state == "TASK_STATE_COMPLETED", task

    def el(path: str, key: str) -> str:
        return stable_id("el", ver(env.project, path, files[path]), key)

    sentence = (
        await cypher(
            env,
            "MATCH (:ArtifactVersion {id: $v})-[:HAS_ELEMENT]->(e:TextUnit {kind: 'sentence'}) RETURN e.id AS id",
            v=ver(env.project, doc_path, files[doc_path]),
        )
    )[0]["id"]
    expected = {
        (el(REQ, "REQ-1"), el(TEST, "MediaCacheTest")),
        (el(REQ, "REQ-1"), el(csv_path, "REQ-7")),
        (el(REQ, "REQ-3"), sentence),
        (el(csv_path, "REQ-8"), sentence),
    }
    query = (
        "MATCH (s:Requirement)<-[:SOURCE]-(l:TraceLink {active: true, projectId: $p})-[:TARGET]->(t) "
        "RETURN s.id AS s, t.id AS t, l.id AS id"
    )
    found = await cypher(env, query, p=env.project)
    assert {(r["s"], r["t"]) for r in found} == expected
    for r in found:
        assert r["id"] == stable_id("tl", APPROACH, APPROACH_VERSION, env.svc.explicit_hash, r["s"], r["t"])
    versions = [
        r["id"]
        for r in await cypher(
            env, "MATCH (:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v) RETURN v.id AS id", p=env.project
        )
    ]
    await env.extract(versions)
    assert {(r["s"], r["t"]) for r in await cypher(env, query, p=env.project)} == expected
    assert (await cypher(env, "MATCH (l:TraceLink {projectId: $p}) RETURN count(l) AS n", p=env.project))[0][
        "n"
    ] == len(expected)


async def test_builtin_disabled_reproduces_stub(env: Any) -> None:
    env.svc.config["builtin"] = {"enabled": False}
    files, result = await extract_mini(env)
    v = ver(env.project, ARCH, files[ARCH])
    assert await cypher(env, SENTENCES, v=v) == []
    rows_ = await cypher(env, "MATCH (:ArtifactVersion {id: $v})-[:HAS_ELEMENT]->(e) RETURN e.kind AS kind", v=v)
    assert rows_ == [{"kind": "file"}]
    run = await cypher(env, "MATCH (r:Run {id: $r}) RETURN r.tool AS tool", r=result["runId"])
    assert run == [{"tool": "stub-extractor"}]


async def test_builtin_failure_falls_back_with_warning(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(path: str, content: bytes) -> Any:
        raise ValueError("bad markdown")

    monkeypatch.setattr("sandman_extraction.text.extract", boom)
    files, result = await extract_mini(env)
    assert result["status"] == "partial" and any("bad markdown" in w for w in result["warnings"])
    v = ver(env.project, ARCH, files[ARCH])
    rows_ = await cypher(env, "MATCH (:ArtifactVersion {id: $v})-[:HAS_ELEMENT]->(e) RETURN e.kind AS kind", v=v)
    assert rows_ == [{"kind": "file"}]


@pytest.mark.slow
async def test_incremental_ingestion_of_1000_files_under_60s(env: Any) -> None:
    import time

    await env.put_project()
    files = {
        f"docs/d{i}.md": f"# Doc {i}\n\nThis is document {i}. It has two sentences.\n".encode() for i in range(1000)
    }
    start = time.monotonic()
    ing = await created(env, files)
    task = await env.extract(ing["createdVersionIds"])
    assert task.state == "TASK_STATE_COMPLETED"
    assert time.monotonic() - start < 60


async def test_reference_equal_to_non_requirement_key_creates_no_link(env: Any) -> None:
    await env.put_project()
    model = (
        b'<repository:Repository xmlns:repository="r" xmlns:xsi="x" id="r" entityName="r">'
        b'<components__Repository id="REQ-9" entityName="Nine"/></repository:Repository>'
    )
    files = env.mini() | {"model/nine.repository": model, "docs/nine.md": b"# Nine\n\nThe design realizes REQ-9.\n"}
    ing = await created(env, files)
    task = await env.extract(ing["createdVersionIds"])
    assert task.state == "TASK_STATE_COMPLETED", task
    query = (
        "MATCH (l:TraceLink {projectId: $p})-[:SOURCE|TARGET]->(e) "
        "WHERE e.name = 'Nine' OR e.text CONTAINS 'REQ-9' RETURN count(l) AS n"
    )
    assert (await cypher(env, query, p=env.project))[0]["n"] == 0


@pytest.mark.parametrize("name", ["plugins", "enrichment"])
def test_invalid_config_fails_at_startup(tmp_path: Path, name: str) -> None:
    from sandman_agent import Settings
    from sandman_agent.contracts import ContractError
    from sandman_extraction.app import DEFAULT_CONFIG_DIR, Service

    shutil.copytree(DEFAULT_CONFIG_DIR, tmp_path / "config")
    (tmp_path / "config" / "extraction" / f"{name}.yaml").write_text("plugins: nope\nenabled: maybe\n")
    settings = Settings(
        service_name="extraction-agent", kg_uri="bolt://x:1", nats_url="nats://x:1",
        artifact_store=tmp_path / "store", config_dir=tmp_path / "config",
    )  # fmt: skip
    with pytest.raises(ContractError, match=name):
        Service(settings)


def raw_tar(entries: list[tuple[str, bytes, bytes | None]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind is not None:
                info.type, info.linkname = kind, "a.txt"
                tf.addfile(info)
            else:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def zipped(entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


HOSTILE = {
    "tar hardlink": raw_tar([("a.txt", b"x", None), ("l", b"", tarfile.LNKTYPE)]),
    "tar symlink": raw_tar([("l", b"", tarfile.SYMTYPE)]),
    "tar chardev": raw_tar([("d", b"", tarfile.CHRTYPE)]),
    "tar fifo": raw_tar([("f", b"", tarfile.FIFOTYPE)]),
    "tar backslash": raw_tar([("..\\x", b"x", None)]),
    "tar drive": raw_tar([("C:\\x", b"x", None)]),
    "tar drive slash": raw_tar([("C:/x", b"x", None)]),
    "tar dotdot": raw_tar([("a/../../x", b"x", None)]),
    "tar absolute": raw_tar([("/x", b"x", None)]),
    "zip backslash": zipped({"..\\x": b"x"}),
    "zip drive": zipped({"C:\\x": b"x"}),
    "zip dotdot": zipped({"../x": b"x"}),
    "zip absolute": zipped({"/x": b"x"}),
}


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_archive_entries_rejected(name: str) -> None:
    with pytest.raises(HTTPException) as e:
        ingest.read_archive(HOSTILE[name], max_bytes=1 << 20)
    assert e.value.status_code == 400 and "archive" in e.value.detail


def test_zip_symlink_and_device_rejected() -> None:
    for mode in (stat.S_IFLNK, stat.S_IFCHR, stat.S_IFIFO):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo("l")
            info.external_attr = (mode | 0o777) << 16
            zf.writestr(info, "a.txt")
        with pytest.raises(HTTPException):
            ingest.read_archive(buf.getvalue(), max_bytes=1 << 20)


def test_encrypted_zip_and_corrupt_gzip_in_tar_are_rejected() -> None:
    raw = bytearray(zipped({"a.txt": b"hello"}))
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        raw[raw.index(sig) + off] |= 0x1
    with pytest.raises(HTTPException, match="corrupt") as e:
        ingest.read_archive(bytes(raw), max_bytes=1 << 20)
    assert e.value.status_code == 400
    rnd = random.Random(0)
    text = " ".join(f"{rnd.choice(['alpha', 'beta', 'gamma'])}{rnd.randrange(50)}" for _ in range(3000)).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("a")
        info.size = len(text)
        tf.addfile(info, io.BytesIO(text))
    gz = bytearray(buf.getvalue())
    gz[1900:1908] = b"\xff" * 8
    with pytest.raises(HTTPException, match="corrupt") as e:
        ingest.read_archive(bytes(gz), max_bytes=1 << 20)
    assert e.value.status_code == 400


def test_zip_bomb_stops_at_caps_with_lying_header() -> None:
    bomb = zipped({"a": b"\0" * (4 << 20)})
    raw = bytearray(bomb)
    for sig, off in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        raw[raw.index(sig) + off : raw.index(sig) + off + 4] = (1).to_bytes(4, "little")
    for data in (bomb, bytes(raw)):
        with pytest.raises(HTTPException, match="exceeds|corrupt"):
            ingest.read_archive(data, max_bytes=1 << 20)
        with pytest.raises(HTTPException, match="exceeds|corrupt"):
            ingest.read_archive(data, max_bytes=1 << 30, max_entry_bytes=1 << 20)


def test_tar_bomb_and_total_cap() -> None:
    bomb = raw_tar([("a", b"\0" * (4 << 20), None)])
    with pytest.raises(HTTPException, match="exceeds"):
        ingest.read_archive(bomb, max_bytes=1 << 20)
    with pytest.raises(HTTPException, match="exceeds"):
        ingest.read_archive(bomb, max_bytes=1 << 30, max_entry_bytes=1 << 20)
    two = raw_tar([("a", b"x" * 600, None), ("b", b"x" * 600, None)])
    with pytest.raises(HTTPException, match="exceeds"):
        ingest.read_archive(two, max_bytes=1000)


def test_entry_count_bomb_rejected_for_tar_and_zip() -> None:
    many = {f"f{i}": b"" for i in range(50)}
    for data in (raw_tar([(n, b"", None) for n in many]), zipped(many)):
        with pytest.raises(HTTPException, match="too many entries"):
            ingest.read_archive(data, max_bytes=1 << 20, max_entries=10)


async def test_archive_only_upload_and_missing_parts(env: Any) -> None:
    await env.put_project()
    url = f"/v1/projects/{env.project}/ingestions"
    assert (await env.http.post(url, data={})).status_code == 400
    empty = await env.http.post(url, files={"archive": ("a", env.tar_gz({}), "application/octet-stream")})
    assert empty.status_code == 400
    files = env.mini()
    resp = await env.http.post(url, files={"archive": ("a", env.tar_gz(files), "application/octet-stream")})
    assert resp.status_code == 201, resp.text
    assert resp.json()["mode"] == "partial" and len(resp.json()["createdVersionIds"]) == len(files)
    assert "sourceRevision" not in resp.json()
    again = await env.http.post(url, files={"archive": ("a", env.tar_gz(files), "application/octet-stream")})
    assert again.status_code == 201 and again.json()["unchangedCount"] == len(files)
    hostile = await env.http.post(url, files={"archive": ("a", HOSTILE["tar hardlink"], "application/octet-stream")})
    assert hostile.status_code == 400


async def test_archive_only_upload_is_partial_and_retires_nothing(env: Any) -> None:
    await env.put_project()
    url = f"/v1/projects/{env.project}/ingestions"
    await created(env, {"a.txt": b"a1", "b.txt": b"b1"})

    async def upload(files: dict[str, bytes]) -> dict[str, Any]:
        resp = await env.http.post(url, files={"archive": ("a", env.tar_gz(files), "application/octet-stream")})
        assert resp.status_code == 201, resp.text
        return resp.json()

    body = await upload({"b.txt": b"b2"})
    assert body["mode"] == "partial" and body["deletedArtifactIds"] == []
    assert body["createdVersionIds"] == [ver(env.project, "b.txt", b"b2")]
    query = "MATCH (a:Artifact {projectId: $p}) RETURN a.path AS path, a.deletedAt AS d"
    rows = await cypher(env, query, p=env.project)
    assert {r["path"]: r["d"] for r in rows} == {"a.txt": None, "b.txt": None}
    again = await upload({"b.txt": b"b2"})
    assert again["unchangedCount"] == 1 and again["createdVersionIds"] == []
