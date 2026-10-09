"""T1.5 acceptance tests: enrichment writes, provenance, failure modes (Testcontainers Memgraph + NATS, fake LLM)."""

import dataclasses
import json
from collections.abc import Callable
from typing import Any

import pytest
from sandman_agent import Contracts, stable_id
from sandman_agent.config import DEFAULT_CONTRACTS_DIR
from sandman_agent.llm import BudgetExceededError, ChatMessage, ChatRequest, ChatResponse, PolicyError, Usage
from sandman_extraction import enrich

REQ = "requirements/requirements.md"
SELECT = (
    "MATCH (e:Element {projectId: $p}) WHERE e:Requirement OR (e:TextUnit AND e.kind = 'section') "
    "AND e.text IS NOT NULL AND trim(e.text) <> '' "
)


def title(req: ChatRequest) -> str:
    return next(line for line in req.messages[0].content.splitlines() if line.startswith("Title: "))[7:]


def default_reply(req: ChatRequest) -> str:
    name = title(req)
    if req.response_schema and "summary" in req.response_schema["properties"]:
        return json.dumps({"summary": f"About {name}"})
    word = name.split()[-1].title()
    return json.dumps(
        {"concepts": [{"name": "Media  Cache", "confidence": 0.9}, {"name": "media cache", "confidence": 0.5},
                      {"name": word, "confidence": 0.7}]}
    )  # fmt: skip


class FakeLlm:
    def __init__(self, reply: Callable[[ChatRequest], str] = default_reply) -> None:
        self.reply, self.requests, self.policies = reply, [], []

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        return ChatResponse(ChatMessage("assistant", self.reply(req)), "stop", Usage(7, 3), "qwen3:1.7b")

    async def embed(self, model: str, texts: Any) -> list[list[float]]:
        raise AssertionError("no embeddings in enrichment")


def enable(env: Any, llm: FakeLlm | None = None, **config: Any) -> FakeLlm:
    llm = llm or FakeLlm()
    env.svc.enrichment = dataclasses.replace(env.svc.enrichment, enabled=True, **config)

    def factory(policy: str) -> FakeLlm:
        llm.policies.append(policy)
        return llm

    env.svc.llm_factory = factory
    return llm


async def cypher(env: Any, query: str, **params: Any) -> list[dict[str, Any]]:
    return await env.svc.kg.read(query, **params)


async def ingest_and_extract(env: Any, files: dict[str, bytes] | None = None, **options: Any) -> Any:
    await env.put_project()
    resp = await env.ingest(files or env.mini())
    assert resp.status_code == 201, resp.text
    return resp.json(), await env.extract(resp.json()["createdVersionIds"], options or None)


async def counts(env: Any) -> dict[str, int]:
    rows = await cypher(
        env,
        "MATCH (c:Concept {projectId: $p}) WITH count(c) AS concepts "
        "OPTIONAL MATCH (:Element {projectId: $p})-[m:MENTIONS]->(:Concept) "
        "RETURN concepts, count(m) AS mentions",
        p=env.project,
    )
    return rows[0]


async def test_enrichment_writes_summaries_concepts_and_provenance(env: Any) -> None:
    llm = enable(env)
    _, task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_COMPLETED", task
    Contracts(DEFAULT_CONTRACTS_DIR).validate("skills.schema.json#/$defs/ExtractArtifactVersionsOutput", task.result)
    assert "warnings" not in task.result
    selected = await cypher(env, SELECT + "RETURN e.id AS id, e.summary AS summary, e.name AS name", p=env.project)
    assert len(selected) >= 4 and all(r["summary"] == f"About {r['name']}" for r in selected)
    assert llm.policies == ["local-only"] and {r.model for r in llm.requests} == {"chat-local"}
    assert len(llm.requests) == 2 * len(selected)
    mentions = await cypher(
        env,
        "MATCH (e:Requirement {projectId: $p, name: 'Cache media files'})-[m:MENTIONS]->(c:Concept) "
        "RETURN c.name AS name, c.id AS id, m.confidence AS conf, m.runId AS run ORDER BY c.name",
        p=env.project,
    )
    assert [(m["name"], m["conf"]) for m in mentions] == [("Files", 0.7), ("Media Cache", 0.9)]
    assert mentions[1]["id"] == stable_id("con", env.project, "media cache")
    run = (
        await cypher(
            env,
            "MATCH (r:Run {projectId: $p, tool: 'llm-enrichment'}) RETURN properties(r) AS r, "
            "[(r)-[:PRODUCED]->(c:Concept) | c.id] AS produced",
            p=env.project,
        )
    )[0]
    props = run["r"]
    assert props["id"] == mentions[0]["run"] and props["status"] == "completed"
    assert (props["model"], props["resolvedModel"], props["skill"]) == (
        "chat-local",
        "qwen3:1.7b",
        "extract-artifact-versions",
    )
    params = json.loads(props["params"])
    assert (params["seed"], params["temperature"], params["model"]) == (42, 0.0, "ollama/qwen3:1.7b")
    assert (params["maxSummaryChars"], params["maxConcepts"], params["maxInputChars"]) == (400, 8, 6000)
    assert props["promptIds"] == enrich.PROMPT_IDS
    assert (props["inputTokens"], props["outputTokens"]) == (7 * len(llm.requests), 3 * len(llm.requests))
    assert props["configHash"] == env.svc.enrichment_hash != env.config_hash
    assert mentions[1]["id"] in run["produced"]
    done = await env.events("sandman.extraction.completed")
    Contracts(DEFAULT_CONTRACTS_DIR).validate("events.schema.json", done[0])


async def test_disabled_makes_no_call_and_no_run(env: Any) -> None:
    llm = enable(env)
    _, task = await ingest_and_extract(env, llmEnrichment=False)
    assert task.state == "TASK_STATE_COMPLETED" and "warnings" not in task.result
    assert llm.requests == []
    env.svc.enrichment = dataclasses.replace(env.svc.enrichment, enabled=False)
    resp = await env.ingest(env.mini() | {REQ: env.mini()[REQ] + b"\n## REQ-4 More\nText.\n"})
    await env.extract(resp.json()["createdVersionIds"])
    assert llm.requests == []
    runs = await cypher(env, "MATCH (r:Run {projectId: $p, tool: 'llm-enrichment'}) RETURN r.id AS id", p=env.project)
    assert runs == []
    assert (await counts(env)) == {"concepts": 0, "mentions": 0}


async def test_rerun_is_idempotent_and_replaces_mentions(env: Any) -> None:
    llm = enable(env)
    ing, _ = await ingest_and_extract(env)
    first = await counts(env)
    assert first["concepts"] > 0
    again = await env.extract(ing["createdVersionIds"])
    assert again.state == "TASK_STATE_COMPLETED"
    assert await counts(env) == first
    assert len(llm.requests) > 0

    def other(req: ChatRequest) -> str:
        if req.response_schema and "concepts" in req.response_schema["properties"]:
            return json.dumps({"concepts": [{"name": "Only One", "confidence": 1.0}]})
        return default_reply(req)

    llm.reply = other
    await env.extract(ing["createdVersionIds"])
    names = await cypher(
        env, "MATCH (:Element {projectId: $p})-[:MENTIONS]->(c:Concept) RETURN DISTINCT c.name AS n", p=env.project
    )
    assert names == [{"n": "Only One"}]


async def test_unchanged_elements_are_reused_in_a_new_version(env: Any) -> None:
    llm = enable(env)
    files = env.mini()
    await ingest_and_extract(env, files)
    calls = len(llm.requests)
    resp = await env.ingest(files | {REQ: files[REQ] + b"\n## REQ-4 New\nSomething new.\n"})
    task = await env.extract(resp.json()["createdVersionIds"])
    assert task.state == "TASK_STATE_COMPLETED"
    assert len(llm.requests) - calls == 2
    reused = await cypher(
        env,
        "MATCH (e:Requirement {projectId: $p, name: 'Cache media files'})<-[:HAS_ELEMENT]-(v:ArtifactVersion) "
        "OPTIONAL MATCH (e)-[:MENTIONS]->(c) RETURN v.id AS v, e.summary AS summary, count(c) AS n",
        p=env.project,
    )
    assert len(reused) == 2 and all(r["summary"] and r["n"] == 2 for r in reused)


async def test_invalid_responses_skip_the_element_with_a_warning(env: Any) -> None:
    def hostile(req: ChatRequest) -> str:
        if "Encrypt user passwords" in req.messages[0].content:
            return "HACKED: ignore previous instructions"
        return default_reply(req)

    enable(env, FakeLlm(hostile))
    _, task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_COMPLETED"
    assert any("skipped invalid responses" in w for w in task.result["warnings"])
    rows = await cypher(
        env,
        "MATCH (r:Requirement {projectId: $p}) RETURN r.name AS name, r.summary AS s ORDER BY r.name",
        p=env.project,
    )
    assert [(r["name"], r["s"] is None) for r in rows] == [
        ("Cache media files", False),
        ("Encrypt user passwords", True),
        ("Upload audio files", False),
    ]
    run = (
        await cypher(env, "MATCH (r:Run {projectId: $p, tool: 'llm-enrichment'}) RETURN r.status AS s", p=env.project)
    )[0]
    assert run["s"] == "partial"


async def test_policy_error_fails_the_run_but_keeps_the_extraction(env: Any) -> None:
    llm = enable(env)

    def deny(policy: str) -> Any:
        raise PolicyError("route chat-default is not allowed for local-only projects")

    env.svc.llm_factory = deny
    _, task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_COMPLETED" and task.result["status"] == "completed"
    assert any("LLM enrichment failed: PolicyError" in w for w in task.result["warnings"])
    assert task.result["elementCount"] > 0 and llm.requests == []
    run = (
        await cypher(
            env,
            "MATCH (r:Run {projectId: $p, tool: 'llm-enrichment'}) RETURN r.status AS s, r.error AS e",
            p=env.project,
        )
    )[0]
    assert run["s"] == "failed" and "PolicyError" in run["e"]
    assert len(await cypher(env, "MATCH (r:Requirement {projectId: $p}) RETURN r", p=env.project)) == 3


async def test_gateway_error_fails_the_run_but_keeps_the_extraction(env: Any) -> None:
    class Down(FakeLlm):
        async def chat(self, req: ChatRequest) -> ChatResponse:
            raise ConnectionError("gateway unreachable")

    enable(env, Down())
    _, task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_COMPLETED"
    assert any("ConnectionError" in w for w in task.result["warnings"])
    assert (
        await cypher(env, "MATCH (r:Run {projectId: $p, tool: 'llm-enrichment'}) RETURN r.status AS s", p=env.project)
    ) == [{"s": "failed"}]


async def test_budget_exhaustion_blocks_the_workflow(env: Any) -> None:
    class Broke(FakeLlm):
        async def chat(self, req: ChatRequest) -> ChatResponse:
            raise BudgetExceededError("budget_exceeded")

    enable(env, Broke())
    _, task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_FAILED"
    assert task.error is not None and "BUDGET_EXCEEDED" in json.dumps(task.error)
    assert len(await cypher(env, "MATCH (r:Requirement {projectId: $p}) RETURN r", p=env.project)) == 3
    assert (
        await cypher(env, "MATCH (r:Run {projectId: $p, tool: 'llm-enrichment'}) RETURN r.status AS s", p=env.project)
    ) == [{"s": "failed"}]


async def test_element_limit_is_a_warning(env: Any) -> None:
    llm = enable(env, max_elements=2)
    _, task = await ingest_and_extract(env)
    assert any("limited to the first 2" in w for w in task.result["warnings"])
    assert len(llm.requests) == 4


@pytest.mark.parametrize("budget_title", ["Cache media files", "Upload audio files", "Encrypt user passwords"])
async def test_budget_exhaustion_wins_over_other_failures(env: Any, budget_title: str) -> None:
    class Mixed(FakeLlm):
        async def chat(self, req: ChatRequest) -> ChatResponse:
            if title(req) == budget_title:
                raise BudgetExceededError("budget_exceeded")
            raise ConnectionError("gateway unreachable")

    enable(env, Mixed())
    _, task = await ingest_and_extract(env)
    assert task.state == "TASK_STATE_FAILED"
    assert task.error is not None and "BUDGET_EXCEEDED" in json.dumps(task.error)


async def test_long_texts_are_cut_with_a_warning(env: Any) -> None:
    llm = enable(env, max_input_chars=200)
    files = env.mini() | {"docs/long.md": b"# Long\n\n" + b"word " * 400}
    _, task = await ingest_and_extract(env, files)
    assert any("first 200 characters of 1 elements" in w for w in task.result["warnings"])
    assert max(len(r.messages[0].content) for r in llm.requests) < 1200
    assert all(r.messages[0].content.rstrip().endswith("ARTIFACT_TEXT>>>") for r in llm.requests)


@pytest.mark.parametrize("change", ["summary-limit", "model-mapping"])
async def test_reuse_is_invalidated_by_limits_and_model_mapping(env: Any, change: str) -> None:
    llm = enable(env)
    files = env.mini()
    await ingest_and_extract(env, files)
    calls = len(llm.requests)
    if change == "summary-limit":
        env.svc.enrichment = dataclasses.replace(env.svc.enrichment, max_summary_chars=300)
    else:
        env.svc.selector.routes["chat-local"] = "ollama/qwen3:4b"
    resp = await env.ingest(files | {REQ: files[REQ] + b"\n## REQ-4 New\nSomething new.\n"})
    task = await env.extract(resp.json()["createdVersionIds"])
    assert task.state == "TASK_STATE_COMPLETED"
    assert len(llm.requests) - calls == 2 * 4


async def test_partial_enrichment_is_not_reused(env: Any) -> None:
    def flaky(req: ChatRequest) -> str:
        if "concepts" in req.response_schema["properties"] and title(req) == "Encrypt user passwords":
            return "not json"
        return default_reply(req)

    llm = enable(env, FakeLlm(flaky))
    files = env.mini()
    await ingest_and_extract(env, files)
    llm.reply = default_reply
    calls = len(llm.requests)
    resp = await env.ingest(files | {REQ: files[REQ] + b"\n## REQ-4 New\nSomething new.\n"})
    await env.extract(resp.json()["createdVersionIds"])
    assert len(llm.requests) - calls == 2 * 4
    rows = await cypher(
        env,
        "MATCH (r:Requirement {projectId: $p, name: 'Encrypt user passwords'})-[:MENTIONS]->(c) RETURN count(c) AS n",
        p=env.project,
    )
    assert rows[0]["n"] >= 2
