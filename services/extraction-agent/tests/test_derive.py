"""T2.7 derive-architecture-model: ExArch port vectors, output validation, reuse key, skill behavior."""

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sandman_agent import A2aClient, TaskSnapshot
from sandman_agent.ids import canonical_json, sha256_hex
from sandman_agent.llm import ChatMessage, ChatRequest, ChatResponse, PolicyError, Usage
from sandman_extraction import derive

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "exarch" / "vectors.json").read_text(encoding="utf-8"))
URL = "http://extraction-agent:8000"
DOC = "docs/architecture.md"
LISTING = ".sandman/derived/exarch/docs/architecture.md.components"
CFG = derive.DeriveConfig()


@pytest.mark.parametrize("vector", VECTORS, ids=range(len(VECTORS)))
def test_vector(vector: dict[str, Any]) -> None:
    names, dropped = derive.postprocess(vector["answer"])
    kept = [n for n in vector["names"] if "::" not in n and "\n" not in n]
    assert names == kept
    assert len(dropped) == len(vector["names"]) - len(kept)


def test_names_with_separator_or_line_break_are_dropped() -> None:
    names, dropped = derive.postprocess("- A::B\n- C")
    assert (names, dropped) == (["C"], ["A::B"])


def test_validation_drops_long_and_control_names_and_caps_the_count() -> None:
    cfg = dataclasses.replace(CFG, max_name_length=5, max_components=2)
    kept, warnings = derive.validate(["Abcdefg", "A\tb", "B", "C", "D"], cfg)
    assert kept == ["B", "C"]
    assert len(warnings) == 3


def test_listing_is_name_pairs_with_newline_endings() -> None:
    assert derive.listing(["A", "B C"]) == b"A::A\nB C::B C\n"


def test_prompts_are_ported_verbatim() -> None:
    assert derive.PROMPTS[0].count("%s") == 1 and derive.PROMPTS[0].endswith("%s\n")
    assert derive.PROMPTS[1].endswith("- Name2\n")
    assert all(i.startswith("prompts/exarch-doc-") for i in derive.PROMPT_IDS)


class FakeLlm:
    def __init__(self, answer: str = "- Alpha\n- beta service\n- Alpha") -> None:
        self.answer, self.requests, self.policies = answer, [], []

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        text = "elaboration" if len(req.messages) == 1 else self.answer
        return ChatResponse(ChatMessage("assistant", text), "stop", Usage(5, 2), "qwen3:1.7b")

    async def embed(self, model: str, texts: Any) -> list[list[float]]:
        raise AssertionError("no embeddings")


def enable(env: Any, llm: FakeLlm) -> FakeLlm:
    def factory(policy: str) -> FakeLlm:
        llm.policies.append(policy)
        return llm

    env.svc.llm_factory = factory
    return llm


async def derive_task(env: Any) -> TaskSnapshot:
    client = A2aClient(env.tokens["orchestrator"], transport=httpx.ASGITransport(app=env.app))
    try:
        task = await client.submit(URL, "derive-architecture-model", {"projectId": env.project})
        for _ in range(300):
            if task.terminal:
                return task
            await asyncio.sleep(0.1)
            task = await client.get(URL, task.task_id)
        raise TimeoutError(task)
    finally:
        await client.close()


async def prepare(env: Any, files: dict[str, bytes] | None = None) -> None:
    await env.put_project()
    resp = await env.ingest(files or {DOC: b"# Architecture\n\nThe Alpha component stores data. Beta calls Alpha.\n"})
    assert resp.status_code == 201, resp.text
    task = await env.extract(resp.json()["createdVersionIds"])
    assert task.state == "TASK_STATE_COMPLETED", task


async def current(env: Any) -> list[dict[str, Any]]:
    return await env.svc.kg.read(
        "MATCH (a:Artifact {projectId: $p, origin: 'derived'}) "
        "OPTIONAL MATCH (a)-[:CURRENT_VERSION]->(v) RETURN a.path AS path, v.id AS vid, v.derivedFrom AS src",
        p=env.project,
    )


async def test_derives_listing_with_provenance_and_is_idempotent(env: Any) -> None:
    llm = enable(env, FakeLlm())
    await prepare(env)
    task = await derive_task(env)
    assert task.state == "TASK_STATE_COMPLETED", task
    assert llm.policies == ["local-only"] and {r.model for r in llm.requests} == {"chat-local"}
    assert len(llm.requests) == 2
    assert llm.requests[1].messages[2].content == derive.PROMPTS[1]
    rows = await current(env)
    assert [r["path"] for r in rows] == [LISTING]
    assert task.result["derivedVersionIds"] == [rows[0]["vid"]] and task.result["retiredArtifactIds"] == []
    run = (
        await env.svc.kg.read(
            "MATCH (r:Run {projectId: $p, tool: 'exarch'})-[:USED]->(v) RETURN properties(r) AS r, v.id AS v",
            p=env.project,
        )
    )[0]
    assert run["r"]["toolVersion"] == "2.1.0+x1" and run["r"]["status"] == "completed"
    assert run["r"]["promptIds"] == derive.PROMPT_IDS and run["v"] == rows[0]["src"]
    again = await derive_task(env)
    assert again.state == "TASK_STATE_COMPLETED" and len(llm.requests) == 2
    assert again.result["derivedVersionIds"] == [] and await current(env) == rows


async def test_local_only_without_route_derives_nothing_and_asks_no_cloud(env: Any) -> None:
    def deny(policy: str) -> Any:
        raise PolicyError("no route")

    env.svc.llm_factory = deny
    await prepare(env)
    task = await derive_task(env)
    assert task.state == "TASK_STATE_COMPLETED"
    assert task.result["warnings"] == [derive.NO_ROUTE]
    assert await current(env) == []


async def test_user_model_retires_derived_models(env: Any) -> None:
    enable(env, FakeLlm())
    await prepare(env)
    await derive_task(env)
    resp = await env.ingest({"model/arch.json": b"{}"}, mode="partial")
    assert resp.status_code == 201, resp.text
    task = await derive_task(env)
    assert task.state == "TASK_STATE_COMPLETED" and len(task.result["retiredArtifactIds"]) == 1
    assert [r["vid"] for r in await current(env)] == [None]


async def test_deleted_source_retires_derived_model(env: Any) -> None:
    enable(env, FakeLlm())
    await prepare(env)
    await derive_task(env)
    resp = await env.ingest({"README.md": b"# x\n"}, mode="full")
    assert resp.status_code == 201, resp.text
    task = await derive_task(env)
    assert len(task.result["retiredArtifactIds"]) == 1
    assert [r["vid"] for r in await current(env)] == [None]


DOC_V1 = b"# Architecture\n\nThe Alpha component stores data. Beta calls Alpha.\n"
DOC_V2 = b"# Architecture\n\nThe Alpha component stores data. Beta and Gamma call Alpha.\n"
USER_MODEL = "model/arch.components"


async def versions_by_path(env: Any) -> dict[str, str]:
    cur = await env.svc.kg.read(
        "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v) RETURN a.path AS path, v.id AS vid", p=env.project
    )
    return {r["path"]: r["vid"] for r in cur}


async def snapshot(env: Any) -> dict[str, Any]:
    kg, p = env.svc.kg, env.project
    orphans = await kg.read(
        "MATCH (e:Element {projectId: $p}) WHERE NOT (:ArtifactVersion)-[:HAS_ELEMENT]->(e) RETURN e.id AS id", p=p
    )
    stale = await kg.read(
        "MATCH (a:Artifact {projectId: $p, origin: 'derived'})-[:CURRENT_VERSION]->(v) "
        "OPTIONAL MATCH (s:ArtifactVersion {id: v.derivedFrom})<-[:CURRENT_VERSION]-(:Artifact) "
        "WITH v, s WHERE s IS NULL RETURN v.id AS id",
        p=p,
    )
    assert orphans == [] and stale == []
    return await versions_by_path(env)


async def extract_current(env: Any, paths: list[str]) -> None:
    versions = await versions_by_path(env)
    task = await env.extract([versions[p] for p in paths])
    assert task.state == "TASK_STATE_COMPLETED", task


async def test_lifecycle(env: Any) -> None:
    llm = enable(env, FakeLlm())
    await env.put_project()

    async def put(files: dict[str, bytes], mode: str = "full") -> None:
        resp = await env.ingest(files, mode=mode)
        assert resp.status_code == 201, resp.text

    await put({DOC: DOC_V1})
    await extract_current(env, [DOC])
    doc1 = (await snapshot(env))[DOC]
    task = await derive_task(env)
    assert task.state == "TASK_STATE_COMPLETED" and len(llm.requests) == 2
    derived1 = task.result["derivedVersionIds"][0]
    await extract_current(env, [LISTING])
    assert await snapshot(env) == {DOC: doc1, LISTING: derived1}
    row = (
        await env.svc.kg.read(
            "MATCH (a:Artifact {projectId: $p, path: $path})-[:CURRENT_VERSION]->(v) "
            "MATCH (v)-[:HAS_ELEMENT]->(e:ModelElement {kind: 'component'}) "
            "RETURN a.origin AS origin, v.derivedFrom AS src, v.derivationRunId AS run, "
            "collect(e.inferred) AS inferred, collect(e.name) AS names",
            p=env.project, path=LISTING,
        )
    )[0]  # fmt: skip
    assert (row["origin"], row["src"]) == ("derived", doc1) and row["run"]
    assert row["inferred"] == [True, True] and sorted(row["names"]) == ["Alpha", "BetaService"]

    llm.answer = "- Alpha\n- Gamma"
    await put({DOC: DOC_V2})
    await extract_current(env, [DOC])
    doc2 = (await versions_by_path(env))[DOC]
    task = await derive_task(env)
    assert task.state == "TASK_STATE_COMPLETED" and len(llm.requests) == 4
    derived2 = task.result["derivedVersionIds"][0]
    assert derived2 != derived1
    assert await snapshot(env) == {DOC: doc2, LISTING: derived2}

    await put({DOC: DOC_V1})
    task = await derive_task(env)
    assert task.state == "TASK_STATE_COMPLETED" and len(llm.requests) == 4
    assert task.result["derivedVersionIds"] == [derived1]
    assert await snapshot(env) == {DOC: doc1, LISTING: derived1}

    await put({USER_MODEL: b"Gamma::Gamma\n"}, mode="partial")
    await extract_current(env, [USER_MODEL])
    task = await derive_task(env)
    assert task.state == "TASK_STATE_COMPLETED" and len(task.result["retiredArtifactIds"]) == 1
    user = (await snapshot(env))[USER_MODEL]
    assert await snapshot(env) == {DOC: doc1, USER_MODEL: user} and len(llm.requests) == 4

    await put({DOC: DOC_V1})
    task = await derive_task(env)
    assert task.state == "TASK_STATE_COMPLETED" and len(llm.requests) == 4
    assert task.result["derivedVersionIds"] == [derived1]
    assert await snapshot(env) == {DOC: doc1, LISTING: derived1}


async def test_reuse_key_matches_the_run_written_by_derive(env: Any) -> None:
    enable(env, FakeLlm())
    await prepare(env)
    doc = (await snapshot(env))[DOC]
    await derive_task(env)
    key = sha256_hex(
        canonical_json(
            {
                "input": {"projectId": env.project, "versionId": doc},
                "tool": "exarch",
                "configHash": env.svc.derivation_hash,
                "promptIds": derive.PROMPT_IDS,
            }
        )
    )
    runs = await env.svc.kg.read("MATCH (r:Run {projectId: $p, tool: 'exarch'}) RETURN r.inputKey AS k", p=env.project)
    assert [r["k"] for r in runs] == [key]
