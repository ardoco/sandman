"""T1.12: explicit-link re-resolution, stored references, configHash vector (Memgraph via Testcontainers)."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from sandman_agent import stable_id
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.ids import canonical_json, sha256_hex

REQ = "requirements/requirements.md"
TEST = "src/test/java/mini/MediaCacheTest.java"
DOC = "docs/extra.md"
DOC_BYTES = b"# Extra\n\nThe design realizes REQ-1.\n"
LINKS = (
    "MATCH (s:Requirement)<-[:SOURCE]-(l:TraceLink {approach: 'explicit-reference', projectId: $p})-[:TARGET]->(t) "
    "WHERE s.key ENDS WITH '#REQ-1' RETURN s.id AS s, t.id AS t, l.active AS a ORDER BY l.id"
)


def ver(project: str, path: str, data: bytes) -> str:
    return stable_id("ver", stable_id("art", project, path), hashlib.sha256(data).hexdigest())


async def cypher(env: Any, query: str, **params: Any) -> list[dict[str, Any]]:
    return await env.svc.kg.read(query, **params)


async def created(env: Any, files: dict[str, bytes]) -> dict[str, Any]:
    resp = await env.ingest(files)
    assert resp.status_code == 201, resp.text
    return resp.json()


VECTORS = json.loads((DEFAULT_CONTRACTS_DIR / "conformance" / "vectors.json").read_text(encoding="utf-8"))


def test_trace_link_vector_config_hash_and_id() -> None:
    for case in VECTORS["traceLinkId"]["cases"]:
        i = case["input"]
        if "configSource" in case:
            assert canonical_json(case["configSource"]["explicitReferences"]).decode() == case["configCanonical"]
            assert sha256_hex(case["configCanonical"]) == i["configHash"]
        assert (
            stable_id("tl", i["approach"], i["approachVersion"], i["configHash"], i["sourceId"], i["targetId"])
            == case["expected"]
        )


def test_config_files_match_contract_schemas() -> None:
    def retrieve(uri: str) -> Resource[Any]:
        text = (DEFAULT_CONTRACTS_DIR / uri.rsplit("/contracts/", 1)[1]).read_text("utf-8")
        return Resource.from_contents(json.loads(text), default_specification=DRAFT202012)

    registry: Registry[Any] = Registry(retrieve=retrieve)  # type: ignore[call-arg]
    config = Path(__file__).parents[3] / "config" / "extraction"
    for name in ("plugins", "enrichment"):
        schema = json.loads((DEFAULT_CONTRACTS_DIR / "config" / f"extraction-{name}.schema.json").read_text("utf-8"))
        Draft202012Validator(schema, registry=registry).validate(
            yaml.safe_load((config / f"{name}.yaml").read_text("utf-8"))
        )


async def test_planned_skill_not_implemented(env: Any) -> None:
    import httpx
    from sandman_agent import A2aClient

    client = A2aClient("orch-token", transport=httpx.ASGITransport(app=env.app))
    try:
        task = await client.submit("http://extraction-agent:8000", "prune-artifact-data", {"projectId": env.project})
        for _ in range(50):
            if task.terminal:
                break
            await asyncio.sleep(0.1)
            task = await client.get("http://extraction-agent:8000", task.task_id)
    finally:
        await client.close()
    assert task.state == "TASK_STATE_REJECTED" or (task.error or {}).get("code") == "NOT_IMPLEMENTED", task


async def _current_links(env: Any) -> list[tuple[str, str]]:
    return [(r["s"], r["t"]) for r in await cypher(env, LINKS, p=env.project) if r["a"]]


async def _extract(env: Any, ing: dict[str, Any]) -> dict[str, Any]:
    task = await env.extract(ing["createdVersionIds"] + ing["reactivatedVersionIds"])
    assert task.state == "TASK_STATE_COMPLETED", task
    return task.result


async def _sentence(env: Any) -> str:
    rows = await cypher(
        env,
        "MATCH (:ArtifactVersion {id: $v})-[:HAS_ELEMENT]->(e:TextUnit {kind: 'sentence'}) RETURN e.id AS id",
        v=ver(env.project, DOC, DOC_BYTES),
    )
    return rows[0]["id"]


async def test_references_stored_with_element(env: Any) -> None:
    await env.put_project()
    files = env.mini() | {DOC: DOC_BYTES}
    await _extract(env, await created(env, files))
    sentence = await cypher(env, "MATCH (e:Element {id: $i}) RETURN e.references AS r", i=await _sentence(env))
    assert json.loads(sentence[0]["r"]) == [{"linkType": "relates", "text": "REQ-1"}]
    plain = await cypher(
        env, "MATCH (e:Element {projectId: $p}) WHERE e.references IS NULL RETURN count(e) AS n", p=env.project
    )
    assert plain[0]["n"] > 0


async def test_requirements_change_delete_revert_and_idempotency(env: Any) -> None:
    await env.put_project()
    v1 = env.mini() | {DOC: DOC_BYTES}
    await _extract(env, await created(env, v1))
    sentence, mediatest = await _sentence(env), stable_id("el", ver(env.project, TEST, v1[TEST]), "MediaCacheTest")
    req1 = stable_id("el", ver(env.project, REQ, v1[REQ]), "REQ-1")
    assert set(await _current_links(env)) == {(req1, sentence), (req1, mediatest)}

    v2 = v1 | {REQ: v1[REQ].replace(b"first download", b"first access")}
    out = await _extract(env, await created(env, v2))
    req1_v2 = stable_id("el", ver(env.project, REQ, v2[REQ]), "REQ-1")
    assert set(await _current_links(env)) == {(req1_v2, sentence), (req1_v2, mediatest)}
    assert out["explicitLinkCount"] == 2
    old = await cypher(env, LINKS, p=env.project)
    assert {(r["s"], r["a"]) for r in old if r["s"] == req1} == {(req1, False)}

    v3 = v2 | {
        REQ: v2[REQ].replace(b"## REQ-1 Cache media files\nMedia files are cached after the first access.\n", b"")
    }
    await _extract(env, await created(env, v3))
    assert await _current_links(env) == []

    back = await created(env, v2)
    assert back["reactivatedVersionIds"] == [ver(env.project, REQ, v2[REQ])]
    await _extract(env, back)
    assert set(await _current_links(env)) == {(req1_v2, sentence), (req1_v2, mediatest)}

    again = await _extract(env, back)
    assert again["explicitLinkCount"] == 0
    count = await cypher(env, "MATCH (l:TraceLink {projectId: $p}) RETURN count(l) AS n", p=env.project)
    assert count[0]["n"] == 4  # v1 and v2 variants of both links, no duplicates


async def test_links_of_elements_without_stored_references_survive_re_resolution(env: Any) -> None:
    await env.put_project()
    v1 = env.mini() | {DOC: DOC_BYTES}
    await _extract(env, await created(env, v1))
    sentence = await _sentence(env)
    req1 = stable_id("el", ver(env.project, REQ, v1[REQ]), "REQ-1")

    async def strip(tx: Any) -> None:  # an element written before T1.12 has no references property
        await (await tx.run("MATCH (e:Element {id: $i}) REMOVE e.references", i=sentence)).consume()

    await env.svc.kg.write_tx(strip)
    await _extract(env, await created(env, v1 | {REQ: v1[REQ].replace(b"first download", b"first access")}))
    rows = await cypher(env, LINKS, p=env.project)
    assert [(r["s"], r["t"], r["a"]) for r in rows if r["t"] == sentence] == [(req1, sentence, True)]


async def test_re_resolution_runs_only_for_requirements_artifacts_and_counts_changes(
    env: Any, monkeypatch: Any
) -> None:
    from sandman_extraction.extract import ExtractSkill

    calls = []
    real = ExtractSkill._re_resolve

    async def counting(self: Any, *args: Any) -> int:
        calls.append(args)
        return await real(self, *args)

    monkeypatch.setattr(ExtractSkill, "_re_resolve", counting)
    await env.put_project()
    v1 = env.mini() | {DOC: DOC_BYTES}
    ing = await created(env, v1)
    first = await _extract(env, ing)
    assert len(calls) == 1 and first["explicitLinkCount"] == 2
    again = await _extract(env, ing)  # same links found again: nothing newly activated
    assert again["explicitLinkCount"] == 0 and len(calls) == 2
    calls.clear()
    await _extract(env, await created(env, v1 | {DOC: DOC_BYTES + b"More text.\n"}))
    assert calls == []
