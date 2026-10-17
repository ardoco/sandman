"""Element embeddings (T4.1b, ADR-0018): one vector per selected element, written after extraction and enrichment.

Own Run (tool element-embedding). Vectors of unchanged elements (same key, name, contentHash and model id) are copied
from an earlier version, so only changed content reaches the gateway. Failures never touch the extraction result,
only budget exhaustion propagates.
"""

import asyncio
import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from neo4j import AsyncManagedTransaction
from sandman_agent import RunRecorder, SkillContext
from sandman_agent.ids import canonical_json, sha256_hex
from sandman_agent.llm import BudgetExceededError, Usage

from sandman_extraction.ingest import rows

if TYPE_CHECKING:
    from sandman_extraction.app import Service

log = logging.getLogger(__name__)

TOOL, TOOL_VERSION = "element-embedding", "0"
LABELS = ("Requirement", "TextUnit", "ModelElement", "CodeUnit")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class EmbedConfig:
    """config/extraction/embedding.yaml."""

    enabled: bool = False
    dimension: int = 768
    elements: tuple[tuple[str, str | None], ...] = (
        ("Requirement", None),
        ("TextUnit", "section"),
        ("ModelElement", None),
        ("CodeUnit", "type"),
    )
    max_elements: int = 500
    batch_size: int = 32
    concurrency: int = 2
    max_input_chars: int = 6000

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EmbedConfig":
        """Missing keys keep their defaults; a missing file (empty dict) is disabled."""
        d = cls()
        return cls(
            enabled=bool(raw.get("enabled", False)),
            dimension=int(raw.get("dimension", d.dimension)),
            elements=tuple((e["label"], e.get("kind")) for e in raw.get("elements", [])) or d.elements,
            max_elements=int(raw.get("maxElementsPerRun", d.max_elements)),
            batch_size=max(1, int(raw.get("batchSize", d.batch_size))),
            concurrency=max(1, int(raw.get("concurrency", d.concurrency))),
            max_input_chars=max(1, int(raw.get("maxInputChars", d.max_input_chars))),
        )


def embedding_text(name: str, text: str | None, cfg: EmbedConfig) -> str:
    """'name', a blank line and the text; control characters removed, cut to maxInputChars."""
    body = f"{name}\n\n{text}" if (text or "").strip() else name
    return _CONTROL.sub(" ", body)[: cfg.max_input_chars]


def fit(vector: list[float], dimension: int) -> list[float]:
    """Cut to `dimension` and re-normalize (Matryoshka models); a shorter vector is an error."""
    if len(vector) < dimension:
        raise ValueError(f"embedding has {len(vector)} dimensions, the index needs {dimension}")
    if len(vector) == dimension:
        return [float(x) for x in vector]
    cut = vector[:dimension]
    norm = math.sqrt(sum(x * x for x in cut)) or 1.0
    return [x / norm for x in cut]


async def _select(
    svc: "Service", vids: list[str], cfg: EmbedConfig
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    found = await svc.kg.read(
        "MATCH (v:ArtifactVersion)-[:HAS_ELEMENT]->(e:Element) WHERE v.id IN $vids "
        "AND any(l IN labels(e) WHERE l IN $labels) "
        "RETURN v.id AS vid, e.id AS id, e.key AS key, e.name AS name, e.text AS text, e.kind AS kind, "
        "labels(e) AS labels, e.contentHash AS hash, e.embeddingModel AS model ORDER BY e.id",
        vids=vids, labels=list(LABELS),
    )  # fmt: skip
    wanted = [
        r
        for r in found
        if (r["name"] or "").strip()
        and any(label in r["labels"] and kind in (None, r["kind"]) for label, kind in cfg.elements)
    ]
    warnings = []
    if len(wanted) > cfg.max_elements:
        warnings.append(f"embedding limited to the first {cfg.max_elements} of {len(wanted)} elements")
        wanted = wanted[: cfg.max_elements]
    by_version: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in wanted:
        by_version[r["vid"]].append(r)
    return by_version, warnings


async def _reusable(svc: "Service", pid: str, els: list[dict[str, Any]], model: str) -> dict[str, dict[str, Any]]:
    """Vector of an element with the same key, name, contentHash and model id (earlier version or itself)."""
    found = await svc.kg.read(
        "UNWIND $els AS x MATCH (old:Element {projectId: $pid, key: x.key, contentHash: x.hash, name: x.name}) "
        "WHERE old.embedding IS NOT NULL AND old.embeddingModel = $model "
        "WITH x, old ORDER BY old.id WITH x, head(collect(old)) AS src "
        "RETURN x.id AS id, src.id AS srcId, src.embedding AS vector",
        els=[{"id": e["id"], "key": e["key"], "hash": e["hash"], "name": e["name"]} for e in els if e["hash"]],
        pid=pid, model=model,
    )  # fmt: skip
    return {r["id"]: r for r in found}


async def _write(svc: "Service", model: str, items: list[dict[str, Any]]) -> None:
    async def work(tx: AsyncManagedTransaction) -> None:
        await rows(
            tx,
            "UNWIND $items AS x MATCH (e:Element {id: x.id}) SET e.embedding = x.vector, e.embeddingModel = $model",
            items=items, model=model,
        )  # fmt: skip

    await svc.kg.write_tx(work)


async def embed(
    svc: "Service",
    ctx: SkillContext,
    runs: RunRecorder,
    common: dict[str, Any],
    skill_input: dict[str, Any],
    pid: str,
    llm_policy: str,
    vids: list[str],
) -> list[str]:
    """Embed the elements of the versions; returns warnings. Only BudgetExceededError propagates."""
    cfg = svc.embedding
    if not cfg.enabled or skill_input.get("options", {}).get("embedding") is False:
        return []
    by_version, warnings = await _select(svc, vids, cfg)
    if not by_version:
        return warnings
    route = svc.embed_route(llm_policy)
    if not svc.llm_configured or (svc.selector is not None and route not in svc.selector.routes):
        return [*warnings, f"embedding skipped: no LLM access or route {route} configured"]
    model = svc.route_model(route)
    params = canonical_json(
        {"model": model, "dimension": cfg.dimension, "batchSize": cfg.batch_size, "maxInputChars": cfg.max_input_chars}
    ).decode()
    input_key = sha256_hex(canonical_json({"input": skill_input, "tool": TOOL, "configHash": svc.embedding_hash}))
    await runs.cleanup_stale(input_key)
    run_id = await runs.start(
        tool=TOOL, tool_version=TOOL_VERSION, input_key=input_key, used_version_ids=list(by_version), model=route,
        params=params, **{**common, "config_hash": svc.embedding_hash},
    )  # fmt: skip
    failures: list[str] = []
    try:
        client = svc.llm_client(llm_policy)
        pending: list[tuple[str, dict[str, Any]]] = []
        for vid, els in by_version.items():
            reuse = await _reusable(svc, pid, els, model)
            copied = [
                {"id": e["id"], "vector": reuse[e["id"]]["vector"]}
                for e in els
                if e["id"] in reuse and reuse[e["id"]]["srcId"] != e["id"]
            ]
            if copied:
                await _write(svc, model, copied)
            pending += [(vid, e) for e in els if e["id"] not in reuse]
        batches = [pending[i : i + cfg.batch_size] for i in range(0, len(pending), cfg.batch_size)]
        semaphore, done = asyncio.Semaphore(cfg.concurrency), 0

        async def one(batch: list[tuple[str, dict[str, Any]]]) -> None:
            nonlocal done
            async with semaphore:
                texts = [embedding_text(e["name"], e["text"], cfg) for _, e in batch]
                vectors = await client.embed(route, texts)
                if len(vectors) != len(texts):
                    raise ValueError(f"{len(vectors)} embeddings for {len(texts)} texts")
                items = [
                    {"id": e["id"], "vector": fit(v, cfg.dimension)} for (_, e), v in zip(batch, vectors, strict=True)
                ]
                await _write(svc, model, items)
                done += len(batch)
                await ctx.progress(f"embedding elements ({done}/{len(pending)})")

        outcomes = await asyncio.gather(*(one(b) for b in batches), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BudgetExceededError):
                raise outcome
        failures = [f"{type(o).__name__}: {o}" for o in outcomes if isinstance(o, BaseException)]
    except BudgetExceededError as e:
        await runs.fail(run_id, f"BudgetExceededError: {e}")
        raise
    except Exception as e:  # noqa: BLE001 - embedding is optional, any gateway or policy failure keeps the extraction
        log.warning("element embedding failed", extra={"fields": {"error": type(e).__name__}})
        await runs.fail(run_id, f"{type(e).__name__}: {e}")
        return [*warnings, f"embedding failed: {type(e).__name__}: {e}"]
    await runs.complete(run_id, Usage(), "partial" if failures else "completed", model)
    if failures:
        warnings.append(f"embedding failed for {len(failures)} batches, e.g. {failures[0]}")
    return warnings
