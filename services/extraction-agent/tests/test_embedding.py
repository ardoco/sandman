"""T4.1b: element embeddings (unit helpers; Testcontainers Memgraph 3.13.1 + NATS with a fake embedding client)."""

import dataclasses
import hashlib
import math
from typing import Any

import pytest
from sandman_agent.llm import BudgetExceededError, ChatRequest, ChatResponse, PolicyError
from sandman_extraction.embed import EmbedConfig, embedding_text, fit

REQ = "requirements/requirements.md"
LOCAL_MODEL = "ollama/nomic-embed-text:v1.5"
CLOUD_MODEL = "openai/text-embedding-3-small"


def test_default_config_matches_index_dimension() -> None:
    from sandman_agent.config import DEFAULT_CONTRACTS_DIR

    init = (DEFAULT_CONTRACTS_DIR / "kg" / "init.cypher").read_text(encoding="utf-8")
    assert f'"dimension": {EmbedConfig().dimension}' in init
    assert not EmbedConfig.from_dict({}).enabled


def test_text_is_name_and_text_without_control_characters() -> None:
    cfg = EmbedConfig(max_input_chars=20)
    assert embedding_text("Name", None, cfg) == "Name"
    assert embedding_text("Name", "  ", cfg) == "Name"
    assert embedding_text("Name", "a\x00b", EmbedConfig()) == "Name\n\na b"
    assert len(embedding_text("Name", "x" * 100, cfg)) == 20


def test_fit_cuts_and_normalizes_and_rejects_short_vectors() -> None:
    assert fit([1.0, 2.0, 3.0], 3) == [1.0, 2.0, 3.0]
    cut = fit([3.0, 4.0, 9.0], 2)
    assert cut == pytest.approx([0.6, 0.8]) and math.isclose(sum(x * x for x in cut), 1.0)
    with pytest.raises(ValueError, match="needs 3"):
        fit([1.0], 3)


class FakeEmbedder:
    def __init__(self, dim: int = 768) -> None:
        self.dim, self.calls, self.policies = dim, [], []

    async def chat(self, req: ChatRequest) -> ChatResponse:
        raise AssertionError("no chat in embedding")

    async def embed(self, model: str, texts: Any) -> list[list[float]]:
        self.calls.append((model, list(texts)))
        out = []
        for t in texts:
            seed = hashlib.sha256(t.encode()).digest()
            out.append([seed[i % len(seed)] / 255 + 0.01 for i in range(self.dim)])
        return out

    @property
    def texts(self) -> list[str]:
        return [t for _, ts in self.calls for t in ts]


def enable(env: Any, llm: FakeEmbedder | None = None, **config: Any) -> FakeEmbedder:
    llm = llm or FakeEmbedder()
    env.svc.embedding = dataclasses.replace(env.svc.embedding, enabled=True, **config)

    def factory(policy: str) -> FakeEmbedder:
        llm.policies.append(policy)
        return llm

    env.svc.llm_factory = factory
    return llm


async def cypher(env: Any, query: str, **params: Any) -> list[dict[str, Any]]:
    return await env.svc.kg.read(query, **params)


async def ingest_and_extract(env: Any, files: dict[str, bytes] | None = None, **options: Any) -> Any:
    await env.put_project()
    return await ingest_more(env, files, **options)


async def ingest_more(env: Any, files: dict[str, bytes] | None = None, **options: Any) -> Any:
    resp = await env.ingest(files or env.mini())
    assert resp.status_code == 201, resp.text
    return await env.extract(resp.json()["createdVersionIds"], options or None)


async def reextract(env: Any) -> Any:
    versions = await cypher(
        env, "MATCH (v:ArtifactVersion {projectId: $p}) RETURN v.id AS id ORDER BY v.id", p=env.project
    )
    return await env.extract([v["id"] for v in versions])


async def embedded(env: Any) -> list[dict[str, Any]]:
    return await cypher(
        env,
        "MATCH (e:Element {projectId: $p}) WHERE e.embedding IS NOT NULL "
        "RETURN e.id AS id, e.key AS key, e.name AS name, labels(e) AS labels, e.embeddingModel AS model, "
        "size(e.embedding) AS n ORDER BY e.id",
        p=env.project,
    )


async def test_disabled_by_default_makes_no_call(env: Any) -> None:
    llm = FakeEmbedder()
    env.svc.llm_factory = lambda policy: llm
    task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_COMPLETED" and not llm.calls
    assert await embedded(env) == []
    assert not await cypher(env, "MATCH (r:Run {tool: 'element-embedding'}) RETURN r")


async def test_embeddings_written_with_model_id_and_searchable(env: Any) -> None:
    llm = enable(env)
    task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_COMPLETED", task
    assert "warnings" not in task.result
    rows = await embedded(env)
    assert rows and {r["model"] for r in rows} == {LOCAL_MODEL} and {r["n"] for r in rows} == {768}
    assert llm.policies == ["local-only"] and {m for m, _ in llm.calls} == {"embed-local"}
    kinds = {next(label for label in r["labels"] if label != "Element") for r in rows}
    assert kinds <= {"Requirement", "TextUnit", "ModelElement", "CodeUnit"} and "Requirement" in kinds
    assert not await cypher(
        env, "MATCH (e:Element {projectId: $p, kind: 'sentence'}) WHERE e.embedding IS NOT NULL RETURN e", p=env.project
    )
    assert any(t.startswith("Cache media files") for t in llm.texts), llm.texts[:5]
    run = (
        await cypher(
            env, "MATCH (r:Run {projectId: $p, tool: 'element-embedding'}) RETURN properties(r) AS r", p=env.project
        )
    )[0]["r"]
    assert (run["status"], run["model"], run["resolvedModel"]) == ("completed", "embed-local", LOCAL_MODEL)
    index = await env.svc.kg.run_autocommit("SHOW VECTOR INDEX INFO")
    assert [(i["index_name"], i["dimension"]) for i in index] == [("element_embedding", 768)]
    hits = await cypher(
        env,
        "MATCH (e:Element {projectId: $p, name: 'Cache media files'}) WITH e.embedding AS v "
        "CALL vector_search.search('element_embedding', 50, v) YIELD node, similarity "
        "WITH node, similarity WHERE node.projectId = $p RETURN node.name AS name, similarity ORDER BY similarity DESC",
        p=env.project,
    )
    assert hits[0]["name"] == "Cache media files" and len(hits) == len(rows)


async def test_unchanged_content_is_not_embedded_again(env: Any) -> None:
    llm = enable(env)
    first = await ingest_and_extract(env)
    count = len(llm.texts)
    assert count and first.state == "TASK_STATE_COMPLETED"
    again = await reextract(env)
    assert again.state == "TASK_STATE_COMPLETED" and len(llm.texts) == count
    changed = env.mini()
    changed[REQ] = changed[REQ].replace(b"Users can upload audio files.", b"Users can upload audio and video files.")
    task = await ingest_more(env, changed)
    assert task.state == "TASK_STATE_COMPLETED"
    new = llm.texts[count:]
    assert new and all("upload" in t.lower() or "requirements" in t.lower() for t in new) and len(new) < count
    assert not any("Cache media files" in t for t in new)
    cache = await cypher(
        env,
        "MATCH (e:Element {projectId: $p, name: 'Cache media files'}) "
        "RETURN e.embedding AS v, e.id AS id ORDER BY e.id",
        p=env.project,
    )
    assert all(r["v"] is not None for r in cache)


async def test_new_model_id_embeds_again(env: Any) -> None:
    llm = enable(env)
    await ingest_and_extract(env)
    count = len(llm.texts)
    env.svc.selector.routes["embed-local"] = "ollama/other-embed:1"
    task = await reextract(env)
    assert task.state == "TASK_STATE_COMPLETED" and len(llm.texts) == 2 * count
    assert {r["model"] for r in await embedded(env)} == {"ollama/other-embed:1"}


async def test_cloud_policy_uses_default_route_and_cuts_long_vectors(env: Any) -> None:
    llm = enable(env, FakeEmbedder(1536))
    assert (
        await env.http.put(f"/v1/projects/{env.project}", json={"name": "C", "llmPolicy": "cloud-allowed"})
    ).status_code == 200
    task = await ingest_more(env)
    assert task.state == "TASK_STATE_COMPLETED", task
    assert llm.policies == ["cloud-allowed"] and {m for m, _ in llm.calls} == {"embed-default"}
    rows = await embedded(env)
    assert {r["model"] for r in rows} == {CLOUD_MODEL} and {r["n"] for r in rows} == {768}
    norms = await cypher(
        env,
        "MATCH (e:Element {projectId: $p}) WHERE e.embedding IS NOT NULL "
        "RETURN reduce(s = 0.0, x IN e.embedding | s + x * x) AS sq LIMIT 1",
        p=env.project,
    )
    assert norms[0]["sq"] == pytest.approx(1.0, rel=1e-4)


async def test_batches_and_element_cap(env: Any) -> None:
    llm = enable(env, batch_size=2, max_elements=5)
    task = await ingest_and_extract(env)
    assert any("limited to the first 5" in w for w in task.result["warnings"])
    assert (
        len(llm.texts) == 5
        and [len(ts) for _, ts in llm.calls] == [2, 2, 1]
        or sorted(len(ts) for _, ts in llm.calls) == [1, 2, 2]
    )


async def test_option_turns_embedding_off(env: Any) -> None:
    llm = enable(env)
    task = await ingest_and_extract(env, embedding=False)
    assert task.state == "TASK_STATE_COMPLETED" and not llm.calls


async def test_no_llm_configured_is_a_warning(env: Any) -> None:
    env.svc.embedding = dataclasses.replace(env.svc.embedding, enabled=True)
    env.svc.selector = None
    task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_COMPLETED"
    assert any("embedding skipped" in w for w in task.result["warnings"])


async def test_gateway_and_policy_errors_keep_the_extraction(env: Any) -> None:
    class Down(FakeEmbedder):
        async def embed(self, model: str, texts: Any) -> list[list[float]]:
            raise PolicyError("route not allowed")

    enable(env, Down())
    task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_COMPLETED"
    assert any("embedding failed for" in w or "embedding failed" in w for w in task.result["warnings"])
    assert len(await cypher(env, "MATCH (r:Requirement {projectId: $p}) RETURN r", p=env.project)) == 3
    assert await embedded(env) == []
    assert (
        await cypher(
            env, "MATCH (r:Run {projectId: $p, tool: 'element-embedding'}) RETURN r.status AS s", p=env.project
        )
    ) == [{"s": "partial"}]


async def test_wrong_dimension_fails_the_batch_not_the_task(env: Any) -> None:
    enable(env, FakeEmbedder(10))
    task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_COMPLETED"
    assert any("needs 768" in w for w in task.result["warnings"])
    assert await embedded(env) == []


async def test_budget_exhaustion_blocks_the_workflow(env: Any) -> None:
    class Broke(FakeEmbedder):
        async def embed(self, model: str, texts: Any) -> list[list[float]]:
            raise BudgetExceededError("budget_exceeded")

    enable(env, Broke())
    task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_FAILED"
    assert (
        await cypher(
            env, "MATCH (r:Run {projectId: $p, tool: 'element-embedding'}) RETURN r.status AS s", p=env.project
        )
    ) == [{"s": "failed"}]
    assert len(await cypher(env, "MATCH (r:Requirement {projectId: $p}) RETURN r", p=env.project)) == 3


async def test_init_schema_is_idempotent_and_versioned(env: Any) -> None:
    init = env.svc.settings.contracts_dir / "kg" / "init.cypher"
    snapshot = "MATCH (v:SchemaVersion) RETURN v.id AS id, v.version AS version, v.appliedAt AS at"
    before = await cypher(env, snapshot)
    await env.svc.kg.init_schema(init)
    await env.svc.kg.init_schema(init)
    assert await cypher(env, snapshot) == before and before[0]["version"] == 2
    assert len(await env.svc.kg.run_autocommit("SHOW VECTOR INDEX INFO")) == 1
