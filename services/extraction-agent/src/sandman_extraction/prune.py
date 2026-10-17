"""Skill prune-artifact-data: retention of old artifact versions, their elements, links and runs (T5.2)."""

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from neo4j import AsyncManagedTransaction
from sandman_agent import RunRecorder, SkillContext, SkillResult
from sandman_agent.ids import canonical_json, sha256_hex
from sandman_agent.kg import now

from sandman_extraction.extract import APPROACH
from sandman_extraction.ingest import rows

if TYPE_CHECKING:
    from sandman_extraction.app import Service

SKILL = "prune-artifact-data"
TOOL, TOOL_VERSION = "retention", "0"
BATCH = 50
PRUNE_RUN_DAYS = 90
KEYS = ("links", "elements", "concepts", "versions", "ingestions", "runs")


class _DryRun(Exception):
    def __init__(self, removed: dict[str, int]) -> None:
        self.removed = removed


async def _count(tx: AsyncManagedTransaction, cypher: Any, **params: Any) -> int:
    return int((await rows(tx, cypher, **params))[0]["n"])


async def _delete(tx: AsyncManagedTransaction, match: Any, count: Any, **params: Any) -> int:
    n = await _count(tx, count, **params)
    if n:
        await rows(tx, match, **params)
    return n


async def _remove_versions(tx: AsyncManagedTransaction, pid: str, agent: str, vids: list[str]) -> dict[str, int]:
    p: dict[str, Any] = {"p": pid, "vids": vids, "agent": agent, "approach": APPROACH}
    ingestions = [r["g"] for r in await rows(
        tx, "MATCH (g:Ingestion {projectId: $p})-[:ACTIVATED]->(v:ArtifactVersion) WHERE v.id IN $vids "
        "RETURN DISTINCT g.id AS g", **p)]  # fmt: skip
    concepts = [r["c"] for r in await rows(
        tx, "MATCH (v:ArtifactVersion {projectId: $p})-[:HAS_ELEMENT]->(:Element)-[:MENTIONS]->(c:Concept) "
        "WHERE v.id IN $vids RETURN DISTINCT c.id AS c", **p)]  # fmt: skip
    touched = [r["r"] for r in await rows(
        tx, "MATCH (r:Run {projectId: $p, agent: $agent})-[:PRODUCED]->(x)<-[:HAS_ELEMENT]-(v:ArtifactVersion) "
        "WHERE v.id IN $vids RETURN DISTINCT r.id AS r "
        "UNION MATCH (v:ArtifactVersion)-[:HAS_ELEMENT]->(:Element)<-[:SOURCE|TARGET]-"
        "(l:TraceLink {approach: $approach}) "
        "WHERE v.id IN $vids AND l.runId IS NOT NULL RETURN DISTINCT l.runId AS r "
        "UNION MATCH (v:ArtifactVersion)-[:HAS_ELEMENT]->(:Element)-[m:MENTIONS]->(:Concept) "
        "WHERE v.id IN $vids AND m.runId IS NOT NULL RETURN DISTINCT m.runId AS r", **p)]  # fmt: skip
    removed = dict.fromkeys(KEYS, 0)
    removed["links"] = await _delete(
        tx,
        "MATCH (v:ArtifactVersion {projectId: $p})-[:HAS_ELEMENT]->(:Element)<-[:SOURCE|TARGET]-"
        "(l:TraceLink {approach: $approach}) WHERE v.id IN $vids WITH DISTINCT l DETACH DELETE l",
        "MATCH (v:ArtifactVersion {projectId: $p})-[:HAS_ELEMENT]->(:Element)<-[:SOURCE|TARGET]-"
        "(l:TraceLink {approach: $approach}) WHERE v.id IN $vids RETURN count(DISTINCT l) AS n", **p)  # fmt: skip
    removed["elements"] = await _delete(
        tx,
        "MATCH (v:ArtifactVersion {projectId: $p})-[:HAS_ELEMENT]->(e:Element) WHERE v.id IN $vids "
        "WITH DISTINCT e DETACH DELETE e",
        "MATCH (v:ArtifactVersion {projectId: $p})-[:HAS_ELEMENT]->(e:Element) WHERE v.id IN $vids "
        "RETURN count(DISTINCT e) AS n", **p)  # fmt: skip
    removed["versions"] = await _delete(
        tx,
        "MATCH (v:ArtifactVersion {projectId: $p}) WHERE v.id IN $vids DETACH DELETE v",
        "MATCH (v:ArtifactVersion {projectId: $p}) WHERE v.id IN $vids RETURN count(v) AS n", **p)  # fmt: skip
    q = {"p": pid, "ids": concepts}
    orphans = ("MATCH (c:Concept {projectId: $p}) WHERE c.id IN $ids OPTIONAL MATCH (c)<-[m:MENTIONS]-() "
               "WITH c, count(m) AS n WHERE n = 0 ")  # fmt: skip
    removed["concepts"] = await _delete(
        tx, orphans + "DETACH DELETE c", orphans + "RETURN count(c) AS n", **q)  # fmt: skip
    q = {"p": pid, "ids": ingestions}
    orphans = ("MATCH (g:Ingestion {projectId: $p, eventPublished: true}) WHERE g.id IN $ids "
               "OPTIONAL MATCH (g)-[x:ACTIVATED|DELETED]->() WITH g, count(x) AS n WHERE n = 0 ")  # fmt: skip
    removed["ingestions"] = await _delete(
        tx, orphans + "DETACH DELETE g", orphans + "RETURN count(g) AS n", **q)  # fmt: skip
    q = {"p": pid, "agent": agent, "ids": touched}
    orphans = ("MATCH (r:Run {projectId: $p, agent: $agent}) WHERE r.id IN $ids AND r.status <> 'running' "
               "OPTIONAL MATCH (r)-[x:PRODUCED]->() WITH r, count(x) AS n WHERE n = 0 ")  # fmt: skip
    removed["runs"] = await _delete(tx, orphans + "DETACH DELETE r", orphans + "RETURN count(r) AS n", **q)  # fmt: skip
    return removed


async def _old_prune_runs(tx: AsyncManagedTransaction, pid: str, agent: str, cutoff: str) -> int:
    q = {"p": pid, "agent": agent, "cutoff": cutoff, "skill": SKILL}
    match = "MATCH (r:Run {projectId: $p, agent: $agent, skill: $skill}) WHERE r.endedAt < $cutoff "
    return await _delete(tx, match + "DETACH DELETE r", match + "RETURN count(r) AS n", **q)


class PruneSkill:
    """prune-artifact-data; dry run deletes in one transaction and rolls it back."""

    id = SKILL

    def __init__(self, service: "Service") -> None:
        self.service = service

    async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
        svc = self.service
        kg, pid = svc.kg, input["projectId"]
        keep = input.get("keepVersionsPerArtifact", 20)
        dry_run = input.get("dryRun", True)
        protected = set(input.get("protectedContentHashes", []))
        agent = svc.settings.service_name
        versions = await kg.read(
            "MATCH (a:Artifact {projectId: $p})-[:HAS_VERSION]->(v:ArtifactVersion) "
            "OPTIONAL MATCH (g:Ingestion)-[:ACTIVATED]->(v) WITH a, v, coalesce(max(g.seq), 0) AS seq "
            "OPTIONAL MATCH (a)-[c:CURRENT_VERSION]->(v) "
            "RETURN a.id AS art, v.id AS id, v.contentHash AS hash, seq, c IS NOT NULL AS current "
            "ORDER BY a.id, seq DESC, v.id", p=pid)  # fmt: skip
        window: list[dict[str, Any]] = []
        position: dict[str, int] = {}
        for v in versions:
            n = position[v["art"]] = position.get(v["art"], 0) + 1
            if n > keep and not v["current"] and v["hash"] not in protected:
                window.append(v)
        candidates = [v["id"] for v in window]
        referenced = {r["id"] for r in await kg.read(
            "MATCH (v:ArtifactVersion {projectId: $p})-[:HAS_ELEMENT]->(e:Element) WHERE v.id IN $ids "
            "MATCH (i:Inconsistency {projectId: $p})-[:INVOLVES]->(e) RETURN DISTINCT v.id AS id "
            "UNION MATCH (v:ArtifactVersion {projectId: $p})-[:HAS_ELEMENT]->(e:Element) WHERE v.id IN $ids "
            "MATCH (:Inconsistency {projectId: $p})-[:INVOLVES]->(:TraceLink)-[:SOURCE|TARGET]->(e) "
            "RETURN DISTINCT v.id AS id "
            "UNION MATCH (v:ArtifactVersion {projectId: $p})-[:HAS_ELEMENT]->(e:Element) WHERE v.id IN $ids "
            "MATCH (l:TraceLink {projectId: $p})-[:SOURCE|TARGET]->(e) "
            "WHERE l.status <> 'rejected' AND (l.active = true OR l.status = 'confirmed') "
            "RETURN DISTINCT v.id AS id", p=pid, ids=candidates)}  # fmt: skip
        removable = [vid for vid in candidates if vid not in referenced]
        cutoff = (
            (datetime.now(UTC) - timedelta(days=PRUNE_RUN_DAYS)).isoformat(timespec="seconds").replace("+00:00", "Z")
        )

        async def work(tx: AsyncManagedTransaction, ids: list[str], old_runs: bool) -> dict[str, int]:
            removed = await _remove_versions(tx, pid, agent, ids) if ids else dict.fromkeys(KEYS, 0)
            if old_runs:
                removed["runs"] += await _old_prune_runs(tx, pid, agent, cutoff)
            return removed

        removed = dict.fromkeys(KEYS, 0)
        if dry_run:

            async def rollback(tx: AsyncManagedTransaction) -> None:
                raise _DryRun(await work(tx, removable, True))

            try:
                await kg.write_tx(rollback)
            except _DryRun as e:
                removed = e.removed
        else:
            batches = [removable[i : i + BATCH] for i in range(0, len(removable), BATCH)] or [[]]
            for i, ids in enumerate(batches):

                async def batch(
                    tx: AsyncManagedTransaction, ids: list[str] = ids, last: bool = i == len(batches) - 1
                ) -> dict[str, int]:
                    return await work(tx, ids, last)

                part = await kg.write_tx(batch)
                removed = {k: removed[k] + part[k] for k in KEYS}
        total = sum(removed.values())
        kept = len(versions) - len(removable)
        params = {"keepVersionsPerArtifact": keep, "protectedContentHashes": len(protected), "dryRun": dry_run,
                  "removed": removed}  # fmt: skip
        runs = RunRecorder(kg, agent, svc.status.version)
        run_id = await runs.start(
            project_id=pid, skill=SKILL, tool=TOOL, tool_version=TOOL_VERSION,
            input_key=sha256_hex(canonical_json({"input": input, "tool": TOOL, "at": now()})),
            params=json.dumps(params, sort_keys=True), workflow_id=ctx.workflow_id, task_id=ctx.task_id,
            triggered_by=ctx.actor or f"service:{ctx.caller}",
        )  # fmt: skip
        await runs.complete(run_id)
        output = {"runId": run_id, "removedCount": total, "keptCount": kept, "dryRun": dry_run, "removed": removed}
        event = {"projectId": pid, "scope": "artifact-data", "runId": run_id, "dryRun": dry_run,
                 "keepVersionsPerArtifact": keep, "removedCount": total}  # fmt: skip
        if protected:
            event["protectedContentHashes"] = sorted(protected)
        return SkillResult(output, "sandman.retention.pruned", event)
