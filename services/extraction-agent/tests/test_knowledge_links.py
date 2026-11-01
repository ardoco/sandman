"""T7.2: user-declared links of sandman-knowledge.yaml (Memgraph via Testcontainers)."""

import hashlib
import json
from typing import Any

from sandman_agent import stable_id
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.ids import canonical_json, sha256_hex

VECTORS = json.loads((DEFAULT_CONTRACTS_DIR / "conformance" / "vectors.json").read_text(encoding="utf-8"))
KNOWLEDGE = "sandman-knowledge.yaml"
REQ = "requirements/requirements.md"
TEST = "src/test/java/mini/MediaCacheTest.java"
LINK = {"source": f"{REQ}#REQ-1", "target": f"{TEST}#MediaCacheTest", "type": "verifies"}
FILE = f"""version: 1
elements:
  - id: psp
    label: ModelElement
    kind: component
    name: Payment Provider
aliases:
  - element: {TEST}#MediaCacheTest
    names: [Cache Test]
links:
  - source: {LINK["source"]}
    target: {LINK["target"]}
    type: verifies
    rationale: Checked by hand.
  - source: decl:psp
    target: {REQ}#REQ-1
    type: relates
""".encode()
DECLARED = (
    "MATCH (s)<-[:SOURCE]-(l:TraceLink {approach: 'user-declared', projectId: $p})-[:TARGET]->(t) "
    "RETURN s.key AS s, t.key AS t, l.linkType AS type, l.status AS status, l.confidence AS c, l.active AS a, "
    "l.id AS id, l.rationale AS rationale, l.configHash AS h ORDER BY l.id"
)


def test_trace_link_vector_of_a_declared_link() -> None:
    case = next(c for c in VECTORS["traceLinkId"]["cases"] if "linkEntry" in c)
    i = case["input"]
    assert canonical_json(case["linkEntry"]).decode() == case["linkEntryCanonical"]
    assert sha256_hex(case["linkEntryCanonical"]) == i["configHash"]
    assert (i["approach"], i["approachVersion"]) == ("user-declared", "1")
    assert (
        stable_id("tl", i["approach"], i["approachVersion"], i["configHash"], i["sourceId"], i["targetId"])
        == case["expected"]
    )


def ver(project: str, path: str, data: bytes) -> str:
    return stable_id("ver", stable_id("art", project, path), hashlib.sha256(data).hexdigest())


async def run(env: Any, files: dict[str, bytes], mode: str = "full") -> dict[str, Any]:
    resp = await env.ingest(files, mode)
    assert resp.status_code == 201, resp.text
    ing = resp.json()
    versions = ing["createdVersionIds"] + ing["reactivatedVersionIds"]
    if not versions:
        return {}
    task = await env.extract(versions)
    assert task.state == "TASK_STATE_COMPLETED", task
    return task.result


async def declared(env: Any, active: bool | None = True) -> list[dict[str, Any]]:
    found = await env.svc.kg.read(DECLARED, p=env.project)
    return [r for r in found if active is None or r["a"] == active]


def art(env: Any, path: str) -> str:
    return stable_id("art", env.project, path)


async def test_declared_links_are_active_confirmed_and_follow_new_versions(env: Any) -> None:
    await env.put_project()
    files = env.mini() | {KNOWLEDGE: FILE}
    out = await run(env, files)
    assert "warnings" not in out
    keys = {(r["s"], r["t"], r["type"]) for r in await declared(env)}
    assert keys == {
        (f"{art(env, REQ)}#REQ-1", f"{art(env, TEST)}#MediaCacheTest", "verifies"),
        (f"{art(env, KNOWLEDGE)}#decl:psp", f"{art(env, REQ)}#REQ-1", "relates"),
        (
            f"{art(env, KNOWLEDGE)}#alias:{sha256_hex(f'{TEST}#MediaCacheTest')[:12]}",
            f"{art(env, TEST)}#MediaCacheTest",
            "relates",
        ),
    }
    first = next(r for r in await declared(env) if r["type"] == "verifies")
    assert (first["status"], first["c"], first["rationale"]) == ("confirmed", 1.0, "Checked by hand.")
    assert first["h"] == sha256_hex(canonical_json(LINK))

    v2 = files | {TEST: files[TEST] + b"\n// changed\n"}
    await run(env, v2)
    after = [r for r in await declared(env) if r["type"] == "verifies"]
    new_target = stable_id("el", ver(env.project, TEST, v2[TEST]), "MediaCacheTest")
    assert len(after) == 1 and after[0]["id"] != first["id"]
    ids = await env.svc.kg.read("MATCH (l:TraceLink {id: $i})-[:TARGET]->(t) RETURN t.id AS t", i=after[0]["id"])
    assert ids[0]["t"] == new_target
    assert not any(r["id"] == first["id"] for r in await declared(env))
    assert await run(env, v2) == {}


async def test_unresolved_reference_gives_one_warning_and_no_link(env: Any) -> None:
    await env.put_project()
    bad = FILE.replace(f"{REQ}#REQ-1".encode(), f"{REQ}#REQ-404".encode())
    out = await run(env, env.mini() | {KNOWLEDGE: bad})
    assert out["warnings"] == [f"knowledge: unresolved {REQ}#REQ-404"]
    assert {r["type"] for r in await declared(env)} == {"relates"}
    assert len(await declared(env)) == 1


async def test_reference_to_a_path_of_another_project_resolves_to_nothing(env: Any) -> None:
    mine = env.project
    other = f"o-{mine}"
    await env.put_project(other)
    env.project = other
    await run(env, env.mini() | {"secret/notes.md": b"# Notes\n\nPrivate sentence.\n"})
    env.project = mine
    await env.put_project()
    leak = FILE.replace(f"{REQ}#REQ-1".encode(), b"secret/notes.md#anything")
    out = await run(env, env.mini() | {KNOWLEDGE: leak})
    assert out["warnings"] == ["knowledge: unresolved secret/notes.md#anything"]
    assert {r["type"] for r in await declared(env)} == {"relates"}


async def test_deleting_the_knowledge_file_deactivates_its_links(env: Any) -> None:
    await env.put_project()
    files = env.mini() | {KNOWLEDGE: FILE}
    await run(env, files)
    assert await declared(env)
    resp = await env.ingest(env.mini())
    assert resp.status_code == 201, resp.text
    assert await declared(env) == []
    assert len(await declared(env, None)) == 3
    again = await env.ingest(files)
    await env.extract(again.json()["createdVersionIds"] + again.json()["reactivatedVersionIds"])
    assert len(await declared(env)) == 3
