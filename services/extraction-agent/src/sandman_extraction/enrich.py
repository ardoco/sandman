"""LLM enrichment of extracted elements: summaries and concepts (T1.5, Stage 1 section 5.2).

Runs after the elements of a version are written, in its own Run (tool llm-enrichment). Every response is validated
against a JSON Schema; failures never touch the extraction result, only budget exhaustion propagates.
"""

import asyncio
import copy
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from importlib import resources
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator
from neo4j import AsyncManagedTransaction
from sandman_agent import RunRecorder, normalize_concept_name, stable_id
from sandman_agent.ids import canonical_json, sha256_hex
from sandman_agent.llm import BudgetExceededError, ChatMessage, ChatRequest, LlmClient, Usage

from sandman_extraction.ingest import rows

if TYPE_CHECKING:
    from sandman_extraction.app import Service

log = logging.getLogger(__name__)

TOOL, TOOL_VERSION = "llm-enrichment", "0"
SKILL = "extract-artifact-versions"
ATTEMPTS = 2


@dataclass(frozen=True)
class EnrichConfig:
    """config/extraction/enrichment.yaml."""

    enabled: bool = False
    elements: tuple[tuple[str, str | None], ...] = (("Requirement", None), ("TextUnit", "section"))
    max_elements: int = 200
    concurrency: int = 4
    max_summary_chars: int = 400
    max_concepts: int = 8
    max_tokens: int = 512
    max_input_chars: int = 6000
    seed: int = 42
    reasoning_effort: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EnrichConfig":
        """Missing keys keep their defaults; a missing file (empty dict) is disabled."""
        defaults = cls()
        elements = tuple((e["label"], e.get("kind")) for e in raw.get("elements", [])) or defaults.elements
        return cls(
            enabled=bool(raw.get("enabled", False)),
            elements=elements,
            max_elements=int(raw.get("maxElementsPerRun", defaults.max_elements)),
            concurrency=max(1, int(raw.get("concurrency", defaults.concurrency))),
            max_summary_chars=int(raw.get("maxSummaryChars", defaults.max_summary_chars)),
            max_concepts=int(raw.get("maxConceptsPerElement", defaults.max_concepts)),
            max_tokens=int(raw.get("maxTokens", defaults.max_tokens)),
            max_input_chars=max(200, int(raw.get("maxInputChars", defaults.max_input_chars))),
            seed=int(raw.get("seed", defaults.seed)),
            reasoning_effort=tuple((str(k), str(v)) for k, v in raw.get("reasoningEffort", {}).items()),
        )


def _load(name: str, folder: str, suffix: str) -> str:
    return (resources.files("sandman_extraction") / folder / f"{name}.{suffix}").read_text(encoding="utf-8")


TEMPLATES = {name: _load(name, "prompts", "txt") for name in ("summary", "concepts")}
PROMPT_IDS = [f"prompts/{name}.txt@{sha256_hex(text)[:12]}" for name, text in TEMPLATES.items()]
SCHEMAS = {name: json.loads(_load(name, "schemas", "schema.json")) for name in ("summary", "concepts")}


def schemas(cfg: EnrichConfig) -> dict[str, dict[str, Any]]:
    """Response schemas with the configured length bounds."""
    summary, concepts = copy.deepcopy(SCHEMAS["summary"]), copy.deepcopy(SCHEMAS["concepts"])
    summary["properties"]["summary"]["maxLength"] = cfg.max_summary_chars
    concepts["properties"]["concepts"]["maxItems"] = cfg.max_concepts
    return {"summary": summary, "concepts": concepts}


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_ASCII_WS = re.compile(r"[ \t\n\r\f\v]+")
TITLE_MAX = 200


def _plain(value: str) -> str:
    """Control characters replaced by spaces, ASCII whitespace runs collapsed, trimmed (same rule as concept ids)."""
    return _ASCII_WS.sub(" ", _CONTROL.sub(" ", value)).strip(" \t\n\r\f\v")


def _marker_safe(value: str) -> str:
    return value.replace("ARTIFACT_TEXT", "ARTIFACT-TEXT")


def render(name: str, cfg: EnrichConfig, element_name: str, text: str) -> str:
    """Prompt with only the element's name and text (no ids, run ids or timestamps: stable cassette keys).

    The title is a single sanitized line; the text keeps its newlines but loses control characters, the data markers
    and everything beyond `maxInputChars`, so the rules and the closing marker of the template always stay intact.
    """
    safe_text = _marker_safe(_CONTROL.sub(" ", text))[: cfg.max_input_chars]
    return TEMPLATES[name].format(
        max_chars=cfg.max_summary_chars,
        max_concepts=cfg.max_concepts,
        name=_marker_safe(_plain(element_name))[:TITLE_MAX],
        text=safe_text,
    )


@dataclass
class Answer:
    """Validated response of one call, or None after the failed attempts."""

    data: dict[str, Any] | None
    usage: Usage = Usage()
    resolved: tuple[str, ...] = ()


async def ask(
    client: LlmClient, route: str, prompt: str, schema: dict[str, Any], cfg: EnrichConfig, run_id: str
) -> Answer:
    """One structured call, retried once (next seed) on non-JSON, truncated or schema-invalid output."""
    validator = Draft202012Validator(schema)
    total, resolved = Usage(), []
    for attempt in range(ATTEMPTS):
        request = ChatRequest(
            model=route,
            messages=[ChatMessage("user", prompt)],
            response_schema=schema,
            temperature=0.0,
            seed=cfg.seed + attempt,
            max_tokens=cfg.max_tokens,
            user=run_id,
            reasoning_effort=dict(cfg.reasoning_effort).get(route),
        )
        resp = await client.chat(request)
        total = Usage(total.input_tokens + resp.usage.input_tokens, total.output_tokens + resp.usage.output_tokens)
        resolved.append(resp.resolved_model)
        try:
            data = json.loads(resp.message.content)
        except ValueError:
            continue
        if resp.finish_reason != "length" and isinstance(data, dict) and validator.is_valid(data):
            return Answer(data, total, tuple(resolved))
    return Answer(None, total, tuple(resolved))


@dataclass
class Result:
    """Outcome for one element; None fields mean the call failed validation and the element keeps its old data."""

    id: str
    summary: str | None = None
    concepts: list[dict[str, Any]] | None = None
    reused: bool = False


@dataclass
class Totals:
    usage: Usage = Usage()
    resolved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def add(self, answer: Answer) -> None:
        self.usage = Usage(
            self.usage.input_tokens + answer.usage.input_tokens, self.usage.output_tokens + answer.usage.output_tokens
        )
        self.resolved.extend(m for m in answer.resolved if m)


def _concepts(pid: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Concepts with normalized ids; names differing only in case or whitespace collapse to the highest confidence."""
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        display = _plain(item["name"])
        normalized = normalize_concept_name(display)
        if not normalized:
            continue
        seen = merged.get(normalized)
        if seen is None:
            merged[normalized] = {
                "cid": stable_id("con", pid, normalized),
                "name": display,
                "confidence": float(item["confidence"]),
            }
        else:
            seen["confidence"] = max(seen["confidence"], float(item["confidence"]))
    return list(merged.values())


async def _select(
    svc: "Service", vids: list[str], cfg: EnrichConfig
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    found = await svc.kg.read(
        "MATCH (v:ArtifactVersion)-[:HAS_ELEMENT]->(e:Element) WHERE v.id IN $vids AND (e:Requirement OR e:TextUnit) "
        "RETURN v.id AS vid, e.id AS id, e.key AS key, e.name AS name, e.text AS text, e.kind AS kind, "
        "labels(e) AS labels, e.contentHash AS hash ORDER BY e.id",
        vids=vids,
    )
    wanted = [
        r
        for r in found
        if (r["text"] or "").strip()
        and any(label in r["labels"] and kind in (None, r["kind"]) for label, kind in cfg.elements)
    ]
    warnings = []
    if len(wanted) > cfg.max_elements:
        warnings.append(f"LLM enrichment limited to the first {cfg.max_elements} of {len(wanted)} elements")
        wanted = wanted[: cfg.max_elements]
    truncated = sum(len(r["text"]) > cfg.max_input_chars for r in wanted)
    if truncated:
        warnings.append(f"LLM enrichment used only the first {cfg.max_input_chars} characters of {truncated} elements")
    by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in wanted:
        by_version[r["vid"]].append(r)
    return by_version, warnings


async def _reusable(
    svc: "Service", pid: str, els: list[dict[str, Any]], route: str, params: str
) -> dict[str, dict[str, Any]]:
    """Summary and mentions of unchanged elements (same key and contentHash) from an earlier fully successful run
    with the same route, model mapping, prompts and parameters."""
    found = await svc.kg.read(
        "UNWIND $els AS x MATCH (old:Element {projectId: $pid, key: x.key, contentHash: x.hash}) "
        "WHERE old.id <> x.id AND old.summary IS NOT NULL "
        "MATCH (old)<-[:HAS_ELEMENT]-(:ArtifactVersion)<-[:USED]-(r:Run {tool: $tool}) "
        "WHERE r.status = 'completed' AND r.model = $route AND r.promptIds = $pids "
        "AND r.params = $params "
        "WITH x, old, r ORDER BY r.startedAt DESC, old.id "
        "WITH x, head(collect(old)) AS src "
        "OPTIONAL MATCH (src)-[m:MENTIONS]->(c:Concept) "
        "RETURN x.id AS id, src.summary AS summary, "
        "collect(CASE WHEN c IS NULL THEN null "
        "ELSE {cid: c.id, name: c.name, confidence: m.confidence} END) AS mentions",
        els=[{"id": e["id"], "key": e["key"], "hash": e["hash"]} for e in els if e["hash"]],
        pid=pid, tool=TOOL, route=route, pids=PROMPT_IDS, params=params,
    )  # fmt: skip
    return {r["id"]: r for r in found}


async def _write(svc: "Service", pid: str, rid: str, results: list[Result]) -> None:
    """Summaries, replaced MENTIONS edges, concepts and PRODUCED edges of one version in one transaction."""
    summaries = [{"id": r.id, "summary": r.summary} for r in results if r.summary is not None]
    replaced = [r for r in results if r.concepts is not None]
    mentions = [
        {"eid": r.id, "cid": c["cid"], "name": c["name"], "confidence": c["confidence"]}
        for r in replaced
        for c in r.concepts or []
    ]

    async def work(tx: AsyncManagedTransaction) -> None:
        await rows(tx, "UNWIND $items AS x MATCH (e:Element {id: x.id}) SET e.summary = x.summary", items=summaries)
        await rows(
            tx,
            "MATCH (e:Element)-[m:MENTIONS]->(:Concept) WHERE e.id IN $ids DELETE m",
            ids=[r.id for r in replaced],
        )
        await rows(
            tx,
            "MATCH (r:Run {id: $rid}) UNWIND $mentions AS m MATCH (e:Element {id: m.eid}) "
            "MERGE (c:Concept {id: m.cid}) ON CREATE SET c.projectId = $pid, c.name = m.name "
            "MERGE (e)-[x:MENTIONS]->(c) SET x.confidence = m.confidence, x.runId = $rid "
            "MERGE (r)-[:PRODUCED]->(c)",
            rid=rid, pid=pid, mentions=mentions,
        )  # fmt: skip

    await svc.kg.write_tx(work)


async def _enrich_element(
    client: LlmClient, route: str, cfg: EnrichConfig, run_id: str, pid: str, el: dict[str, Any], totals: Totals
) -> Result:
    result, schema = Result(el["id"]), schemas(cfg)
    summary = await ask(client, route, render("summary", cfg, el["name"], el["text"]), schema["summary"], cfg, run_id)
    totals.add(summary)
    if summary.data is not None:
        result.summary = _plain(summary.data["summary"]) or None
    else:
        totals.skipped.append(f"summary of {el['key']}")
    concepts = await ask(
        client, route, render("concepts", cfg, el["name"], el["text"]), schema["concepts"], cfg, run_id
    )
    totals.add(concepts)
    if concepts.data is not None:
        result.concepts = _concepts(pid, concepts.data["concepts"])
    else:
        totals.skipped.append(f"concepts of {el['key']}")
    return result


async def enrich(
    svc: "Service",
    runs: RunRecorder,
    common: dict[str, Any],
    skill_input: dict[str, Any],
    pid: str,
    llm_policy: str,
    vids: list[str],
) -> list[str]:
    """Enrich the elements of the versions; returns warnings. Only BudgetExceededError propagates."""
    cfg = svc.enrichment
    if not cfg.enabled or skill_input.get("options", {}).get("llmEnrichment") is False:
        return []
    by_version, warnings = await _select(svc, vids, cfg)
    if not by_version:
        return warnings
    if not svc.llm_configured:
        return [*warnings, "LLM enrichment skipped: no LLM access configured"]
    route = svc.chat_route(llm_policy)
    params = canonical_json(
        {
            "temperature": 0.0,
            "seed": cfg.seed,
            "model": svc.route_model(route),
            "reasoningEffort": dict(cfg.reasoning_effort).get(route),
            "maxSummaryChars": cfg.max_summary_chars,
            "maxConcepts": cfg.max_concepts,
            "maxTokens": cfg.max_tokens,
            "maxInputChars": cfg.max_input_chars,
        }
    ).decode()
    input_key = sha256_hex(
        canonical_json({"input": skill_input, "tool": TOOL, "configHash": svc.enrichment_hash, "promptIds": PROMPT_IDS})
    )
    await runs.cleanup_stale(input_key)
    run_id = await runs.start(
        tool=TOOL, tool_version=TOOL_VERSION, input_key=input_key, used_version_ids=list(by_version), model=route,
        params=params, prompt_ids=PROMPT_IDS, **{**common, "config_hash": svc.enrichment_hash},
    )  # fmt: skip
    totals = Totals()
    try:
        client = svc.llm_client(llm_policy)
        semaphore = asyncio.Semaphore(cfg.concurrency)
        results: dict[str, list[Result]] = defaultdict(list)
        pending: list[tuple[str, dict[str, Any]]] = []
        for vid, els in by_version.items():
            reuse = await _reusable(svc, pid, els, route, params)
            for el in els:
                old = reuse.get(el["id"])
                if old is None:
                    pending.append((vid, el))
                else:
                    concepts = [
                        {"cid": m["cid"], "name": m["name"], "confidence": float(m["confidence"])}
                        for m in old["mentions"]
                    ]
                    results[vid].append(Result(el["id"], old["summary"], concepts, reused=True))

        async def one(vid: str, el: dict[str, Any]) -> tuple[str, Result]:
            async with semaphore:
                return vid, await _enrich_element(client, route, cfg, run_id, pid, el, totals)

        done = await asyncio.gather(*(one(v, e) for v, e in pending), return_exceptions=True)
        failures = [o for o in done if isinstance(o, BaseException)]
        for failure in failures:
            if isinstance(failure, BudgetExceededError):
                raise failure
        if failures:
            raise failures[0]
        for outcome in done:
            if not isinstance(outcome, BaseException):
                results[outcome[0]].append(outcome[1])
        for items in results.values():
            await _write(svc, pid, run_id, items)
    except BudgetExceededError as e:
        await runs.fail(run_id, f"BudgetExceededError: {e}")
        raise
    except Exception as e:  # noqa: BLE001 - enrichment is optional, any gateway or policy failure keeps the extraction
        log.warning("LLM enrichment failed", extra={"fields": {"error": type(e).__name__}})
        await runs.fail(run_id, f"{type(e).__name__}: {e}")
        return [*warnings, f"LLM enrichment failed: {type(e).__name__}: {e}"]
    status = "partial" if totals.skipped else "completed"
    await runs.complete(
        run_id, totals.usage, status, totals.resolved[0] if totals.resolved else None, len(set(totals.resolved)) > 1
    )
    if totals.skipped:
        warnings.append(
            f"LLM enrichment skipped invalid responses for {len(totals.skipped)} calls, e.g. {totals.skipped[0]}"
        )
    return warnings
