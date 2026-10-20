"""Skill extract-artifact-versions: stub extraction, explicit references, plugin routing (Stage 2 §4.2)."""

import asyncio
import json
import logging
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable
from heapq import heappop, heappush
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, LiteralString

import httpx
from neo4j import AsyncManagedTransaction
from sandman_agent import A2aPluginClient, PluginError, RunRecorder, SkillContext, SkillError, SkillResult, stable_id
from sandman_agent.ids import canonical_json, sha256_hex
from sandman_agent.kg import now

from sandman_extraction import builtin, documents, embed, enrich, models
from sandman_extraction.bounds import add_warnings
from sandman_extraction.ingest import rows
from sandman_extraction.stub import stub_elements

if TYPE_CHECKING:
    from sandman_extraction.app import Service

log = logging.getLogger(__name__)

SKILL = "extract-artifact-versions"
TOOL, TOOL_VERSION = "stub-extractor", "0"
APPROACH, APPROACH_VERSION = "explicit-reference", "0"
LINK_BATCH = 5000


def _element_query(label: LiteralString) -> LiteralString:
    return (
        "MATCH (v:ArtifactVersion {id: $vid}), (r:Run {id: $rid}) UNWIND $els AS e "
        "MERGE (n:Element {id: e.id}) ON CREATE SET n.runId = $rid "
        "SET n:" + label + " "
        "SET n.key = e.key, n.projectId = $pid, n.kind = e.kind, n.name = e.name, n.qualifiedName = e.qualifiedName, "
        "n.text = e.text, n.sentenceIndex = e.sentenceIndex, n.startLine = e.startLine, "
        "n.endLine = e.endLine, n.decisionStatus = e.decisionStatus, n.decisionDate = e.decisionDate, "
        "n.contentHash = e.contentHash, n.aliases = e.aliases, n.references = e.references, n.inferred = e.inferred "
        "MERGE (v)-[:HAS_ELEMENT]->(n) MERGE (r)-[:PRODUCED]->(n)"
    )


def _relation_query(rel: LiteralString) -> LiteralString:
    return "UNWIND $rels AS x MATCH (s:Element {id: x.s}), (t:Element {id: x.t}) MERGE (s)-[:" + rel + "]->(t)"


LABELS: tuple[LiteralString, ...] = ("Requirement", "TextUnit", "ModelElement", "CodeUnit", "TestCase", "Decision")
RELATIONS: tuple[LiteralString, ...] = ("CONTAINS", "DEPENDS_ON", "PROVIDES", "REQUIRES")
ELEMENT_QUERIES = {label: _element_query(label) for label in LABELS}
RELATION_QUERIES = {rel: _relation_query(rel) for rel in RELATIONS}


def match_references(
    content: bytes, elements: list[dict[str, Any]], patterns: list[str], link_type: str = "relates"
) -> list[dict[str, Any]]:
    """Regex matches as references from the innermost element containing the match line.

    An element without line numbers (a plugin's file element) spans the whole file. A match that only that element
    contains, such as a comment above a class, belongs to the next element with line numbers, if there is one."""
    if not elements:
        return []
    text = content.decode("utf-8", errors="replace")
    newlines = [m.start() for m in re.finditer("\n", text)]
    total = len(newlines) + 1
    spans = [(e.get("startLine") or 1, e.get("endLine") or total) for e in elements]
    is_ranged = [bool(e.get("startLine")) for e in elements]
    ranged = sorted((i for i in range(len(elements)) if is_ranged[i]), key=lambda i: (spans[i][0], -spans[i][1], i))
    ranged_starts = [spans[i][0] for i in ranged]

    found = [(bisect_left(newlines, m.start()) + 1, m[0]) for pattern in patterns for m in re.finditer(pattern, text)]
    source_of: dict[int, int] = {}  # match line -> element index, one sweep over the sorted matched lines
    by_start = sorted(range(len(elements)), key=lambda i: spans[i][0])
    open_spans: list[tuple[int, bool, int]] = []  # heap of (length, not ranged, index): innermost first
    pos = 0
    for line in sorted({line for line, _ in found}):
        while pos < len(by_start) and spans[by_start[pos]][0] <= line:
            i = by_start[pos]
            heappush(open_spans, (spans[i][1] - spans[i][0], not is_ranged[i], i))
            pos += 1
        while open_spans and spans[open_spans[0][2]][1] < line:
            heappop(open_spans)
        source = open_spans[0][2] if open_spans else 0
        if not is_ranged[source] and (k := bisect_right(ranged_starts, line)) < len(ranged):
            source = ranged[k]  # a match only the file element contains: the next element with lines
        source_of[line] = source
    return [{"source": elements[source_of[line]]["localKey"], "text": t, "linkType": link_type} for line, t in found]


async def match_references_off_loop(
    path: str, load: Callable[[], bytes], elements: list[dict[str, Any]], patterns: list[str], link_type: str,
    timeout: float,
) -> list[dict[str, Any]]:  # fmt: skip
    """Reads the file and matches the patterns in a worker thread; after `timeout` seconds the file gets no references.

    ponytail: a thread cannot be stopped; a timed-out pattern runs on until it ends, a process pool would kill it."""

    def work() -> list[dict[str, Any]]:
        return match_references(load(), elements, patterns, link_type)

    try:
        return await asyncio.wait_for(asyncio.to_thread(work), timeout)
    except TimeoutError:
        log.warning("explicit-reference patterns timed out, skipped", extra={"fields": {"path": path}})
        return []


def read_stored(svc: "Service", uri: str) -> bytes:
    """The stored blob, read up to the entry cap that the upload already enforced."""
    with svc.store.open(uri) as f:
        return f.read(svc.max_entry_bytes)


def plugin_for(plugins: list[dict[str, Any]], path: str, kind: str, fmt: str | None) -> dict[str, Any] | None:
    """First plugin whose match lists the kind, the format or a glob matching the path."""
    for p in plugins:
        m = p.get("match", {})
        if kind in m.get("kinds", []) or (fmt and fmt in m.get("formats", [])):
            return p
        if any(PurePosixPath(path).full_match(g) for g in m.get("globs", [])):
            return p
    return None


def _seconds(value: Any) -> float:
    if isinstance(value, str) and value[-1:] in ("s", "m", "h"):
        return float(value[:-1]) * {"s": 1, "m": 60, "h": 3600}[value[-1]]
    return float(value or 600)


class ExtractSkill:
    """extract-artifact-versions; writes one transaction per version for elements, one for its links/relations."""

    id = SKILL

    def __init__(self, service: "Service", plugin_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.service, self.plugin_transport = service, plugin_transport

    async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
        svc = self.service
        kg, pid, vids = svc.kg, input["projectId"], input["artifactVersionIds"]
        project = await kg.read("MATCH (p:Project {id: $p}) RETURN p.llmPolicy AS llmPolicy", p=pid)
        versions = await kg.read(
            "MATCH (a:Artifact)-[:HAS_VERSION]->(v:ArtifactVersion {projectId: $p}) WHERE v.id IN $ids "
            "RETURN v.id AS id, a.id AS art, a.path AS path, a.kind AS kind, a.format AS format, v.storageUri AS uri, "
            "v.derivedFrom AS derivedFrom ORDER BY v.id",
            p=pid, ids=vids,
        )  # fmt: skip
        if not project or len(versions) != len(vids):
            raise SkillError("INVALID_INPUT", f"unknown project or artifact versions in {pid}", False)
        plugins: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        by_id = {p["id"]: p for p in svc.config.get("plugins", [])}
        for v in versions:
            p = plugin_for(list(by_id.values()), v["path"], v["kind"], v["format"])
            plugins[p["id"] if p else None].append(v)

        use_builtin = svc.config.get("builtin", {}).get("enabled", True)
        tool, tool_version = (builtin.TOOL, builtin.TOOL_VERSION) if use_builtin else (TOOL, TOOL_VERSION)

        def input_key(tool: str) -> str:
            return sha256_hex(canonical_json({"input": input, "tool": tool, "configHash": svc.config_hash}))

        runs = RunRecorder(kg, svc.settings.service_name, svc.status.version)
        for key in [tool, *(k for k in plugins if k)]:
            await runs.cleanup_stale(input_key(key))
        common = {"project_id": pid, "skill": SKILL, "config_hash": svc.config_hash, "workflow_id": ctx.workflow_id,
                  "task_id": ctx.task_id, "triggered_by": ctx.actor or f"service:{ctx.caller}"}  # fmt: skip
        run_id = await runs.start(tool=tool, tool_version=tool_version, input_key=input_key(tool),
                                  used_version_ids=vids, **common)  # fmt: skip
        try:
            output = await self._extract(
                ctx, runs, run_id, common, input_key, pid, project[0]["llmPolicy"], plugins, by_id, use_builtin
            )
        except Exception as e:
            await runs.fail(run_id, f"{type(e).__name__}: {e}")
            raise
        await runs.complete(run_id, status=output["status"])
        extra = await enrich.enrich(svc, runs, common, input, pid, project[0]["llmPolicy"], vids)
        extra += await embed.embed(svc, ctx, runs, common, input, pid, project[0]["llmPolicy"], vids)
        if extra:
            output["warnings"] = output.get("warnings", [])
            add_warnings(output["warnings"], extra)
        event = {k: output[k] for k in ("runId", "status", "elementCount", "explicitLinkCount", "pluginRuns")}
        event |= {"projectId": pid, "artifactVersionIds": vids}
        return SkillResult(output, "sandman.extraction.completed", event)

    async def _extract(
        self,
        ctx: SkillContext,
        runs: RunRecorder,
        run_id: str,
        common: dict[str, Any],
        input_key: Any,
        pid: str,
        llm_policy: str,
        plugins: dict[str | None, list[dict[str, Any]]],
        by_id: dict[str, dict[str, Any]],
        use_builtin: bool,
    ) -> dict[str, Any]:
        svc = self.service
        patterns = svc.config.get("explicitReferences", {}).get("patterns", [])
        builtin_config = svc.config.get("builtin", {})
        pattern_timeout = float(svc.config.get("explicitReferences", {}).get("timeoutSeconds", 30))
        elements: dict[str, list[dict[str, Any]]] = {}  # version id -> elements
        refs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        relations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        producer: dict[str, str] = {}
        summaries, warnings, status = [], [], "completed"
        current = await svc.kg.read(
            "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v) "
            "RETURN a.path AS path, a.kind AS kind, a.format AS format, v.id AS id, v.storageUri AS uri "
            "ORDER BY v.id",
            p=pid,
        )

        extracted = 0

        async def stub(v: dict[str, Any]) -> None:
            nonlocal status, extracted
            extracted += 1
            await ctx.progress(f"extracting {v['path']} ({extracted}/{len(current)})")
            document = use_builtin and builtin.document_format(v["path"], v["format"])
            content = b"" if document else svc.store.open(v["uri"]).read()
            try:
                if document:
                    stored = svc.store.path_for(v["uri"]).relative_to(svc.store.root)
                    markdown = await documents.to_markdown(svc.converter, stored, document)
                    content = markdown.encode()
                    elements[v["id"]] = await asyncio.to_thread(builtin.extract_document, v["path"], v["kind"], content)
                elif use_builtin:
                    elements[v["id"]], rels = await asyncio.to_thread(
                        builtin.extract,
                        v["path"],
                        v["kind"],
                        v["format"],
                        content,
                        warnings,
                        builtin_config.get("maxXmlBytes", models.MAX_XML_BYTES),
                    )
                    relations[v["id"]].extend(rels)
                else:
                    elements[v["id"]] = stub_elements(v["path"], v["kind"], content)
            except Exception as e:  # noqa: BLE001 - any parser failure falls back to the file-level element
                log.warning("built-in extractor failed", extra={"fields": {"path": v["path"]}})
                add_warnings(warnings, [f"built-in extractor failed for {v['path']}: {type(e).__name__}: {e}"])
                status = "partial"
                content = b"" if document else content
                elements[v["id"]] = stub_elements(v["path"], v["kind"], content)
            if v.get("derivedFrom"):
                for e in elements[v["id"]]:
                    if e["label"] == "ModelElement":
                        e["inferred"] = True
            refs[v["id"]] = await match_references_off_loop(
                v["path"], lambda: content, elements[v["id"]], patterns, "relates", pattern_timeout
            )
            if document:  # line numbers refer to the Markdown rendering, not to the PDF/DOCX
                builtin.drop_lines(elements[v["id"]])
            producer[v["id"]] = run_id

        for plugin_id, versions in plugins.items():
            if plugin_id is None:
                for v in versions:
                    await stub(v)
                continue
            plugin = by_id[plugin_id]
            prun = await runs.start(tool=plugin_id, tool_version=str(plugin.get("version", "0")),
                                    input_key=input_key(plugin_id), used_version_ids=[v["id"] for v in versions],
                                    **common)  # fmt: skip
            handled = [c for c in current if plugin_for([plugin], c["path"], c["kind"], c["format"])]
            plugin_input = {
                "projectId": pid,
                "llmPolicy": llm_policy,
                "artifacts": [
                    {
                        "artifactVersionId": v["id"],
                        "path": v["path"],
                        "kind": v["kind"],
                        "storagePath": str(svc.store.path_for(v["uri"])),
                        "contentHash": v["uri"][-64:],
                    }
                    | ({"format": v["format"]} if v["format"] else {})
                    for v in versions
                ],  # fmt: skip
                "context": {
                    "currentArtifacts": [
                        {"path": c["path"], "storagePath": str(svc.store.path_for(c["uri"]))} for c in handled
                    ]
                },  # fmt: skip
            }
            await ctx.progress(f"running plugin {plugin_id} on {len(versions)} artifacts")
            try:
                out = await self._call(plugin, plugin_input)
            except (PluginError, OSError) as e:
                log.warning("plugin failed, using file-level fallback", extra={"fields": {"plugin": plugin_id}})
                await runs.fail(prun, str(e))
                summaries.append({"pluginId": plugin_id, "runId": prun, "status": "failed", "error": str(e)})
                warnings.append(f"plugin {plugin_id} failed: {e}")
                status = "partial"
                for v in versions:
                    await stub(v)
                continue
            await runs.complete(prun)
            summaries.append({"pluginId": plugin_id, "runId": prun, "status": "completed"})
            warnings.extend(out.get("warnings", []))
            vid_of = {v["path"]: v["id"] for v in versions}
            for v in versions:
                elements[v["id"]], producer[v["id"]] = [], prun
            for el in out["elements"]:
                if el["path"] in vid_of:
                    elements[vid_of[el["path"]]].append({**el, "parent": el.get("parentLocalKey")})
                else:
                    warnings.append(f"plugin {plugin_id}: element for unknown path {el['path']}")
            for r in out.get("explicitReferences", []):
                if r["source"]["path"] in vid_of:
                    refs[vid_of[r["source"]["path"]]].append(
                        {"source": r["source"]["localKey"], "text": r["referenceText"], "linkType": r["linkType"]}
                    )
            for r in out["relations"]:
                if r["source"]["path"] in vid_of:
                    relations[vid_of[r["source"]["path"]]].append(r)
            for v in versions:  # user decision: REQ-n references must survive plugin output
                if not refs[v["id"]]:
                    link_type = {"code": "implements", "test": "verifies"}.get(v["kind"], "relates")
                    refs[v["id"]] = await match_references_off_loop(
                        v["path"], lambda v=v: read_stored(svc, v["uri"]), elements[v["id"]], patterns, link_type,
                        pattern_timeout,
                    )  # fmt: skip

        meta = {v["id"]: v for vs in plugins.values() for v in vs}
        element_count = 0
        for vid, els in elements.items():
            element_count += await self._write_elements(pid, meta[vid]["art"], vid, producer[vid], els, refs[vid])
        # links and relations after all element sets, so references into this run's other versions resolve
        targets: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for r in sorted(await svc.kg.read(
            "MATCH (:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v)-[:HAS_ELEMENT]->(e:Requirement) "
            "RETURN e.id AS id, e.key AS key "
            "UNION MATCH (v:ArtifactVersion)-[:HAS_ELEMENT]->(e:Requirement) WHERE v.id IN $vids "
            "RETURN e.id AS id, e.key AS key",
            p=pid, vids=list(meta),
        ), key=lambda r: r["id"]):  # fmt: skip
            targets[r["key"].split("#", 1)[1]].append((r["id"], r["key"]))
        version_of = {c["path"]: c["id"] for c in current} | {v["path"]: v["id"] for v in meta.values()}
        link_count = 0
        for vid in elements:
            links = {}
            for ref in refs[vid]:
                referrer = stable_id("el", vid, ref["source"])
                for referenced, key in targets.get(ref["text"], []):
                    if referenced != referrer and key != f"{meta[vid]['art']}#{ref['source']}":
                        # link runs from the referenced requirement to the referring element, never to its definition
                        lid = stable_id("tl", APPROACH, APPROACH_VERSION, svc.explicit_hash, referenced, referrer)
                        links[lid] = {"id": lid, "source": referenced, "target": referrer, "linkType": ref["linkType"]}
            rels = defaultdict(list)
            for r in relations[vid]:
                src, tgt = version_of.get(r["source"]["path"]), version_of.get(r["target"]["path"])
                if src is None or tgt is None:
                    warnings.append(f"unresolved {r['type']} relation from {r['source']['path']}")
                    continue
                rels[r["type"]].append({"s": stable_id("el", src, r["source"]["localKey"]),
                                        "t": stable_id("el", tgt, r["target"]["localKey"])})  # fmt: skip
            link_count += await self._write_links(pid, vid, run_id, list(links.values()), rels)
        holds_requirements = await svc.kg.read(
            "MATCH (a:Artifact {projectId: $p})-[:HAS_VERSION]->(:ArtifactVersion)-[:HAS_ELEMENT]->(:Requirement) "
            "WHERE a.id IN $arts RETURN a.id AS id LIMIT 1",
            p=pid, arts=sorted({m["art"] for m in meta.values()}),
        )  # fmt: skip
        if holds_requirements:  # R-014: only a changed (or emptied) requirements artifact moves link endpoints
            link_count += await self._re_resolve(pid, run_id)
        return {"runId": run_id, "status": status, "elementCount": element_count, "explicitLinkCount": link_count,
                "pluginRuns": summaries} | ({"warnings": warnings} if warnings else {})  # fmt: skip

    async def _call(self, plugin: dict[str, Any], plugin_input: dict[str, Any]) -> dict[str, Any]:
        token = Path(plugin["tokenFile"]).read_text(encoding="utf-8").strip()
        client = A2aPluginClient(plugin["url"], token, self.service.contracts, timeout=_seconds(plugin.get("timeout")),
                                 transport=self.plugin_transport)  # fmt: skip
        try:
            return await client.extract(plugin_input)
        finally:
            await client.close()

    async def _write_elements(
        self, pid: str, art: str, vid: str, rid: str, els: list[dict[str, Any]], refs: list[dict[str, Any]]
    ) -> int:
        """Replace the version's element set in one transaction."""
        unique: dict[str, dict[str, Any]] = {}
        for e in els:
            unique.setdefault(e["localKey"], e)
        stored: dict[str, list[dict[str, str]]] = defaultdict(list)
        for ref in refs:
            item = {"text": ref["text"], "linkType": ref["linkType"]}
            if item not in stored[ref["source"]]:
                stored[ref["source"]].append(item)
        by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for key, e in unique.items():
            by_label[e["label"]].append({
                "id": stable_id("el", vid, key), "key": f"{art}#{key}", "kind": e["kind"], "name": e["name"],
                "qualifiedName": e.get("qualifiedName"), "text": e.get("text"), "startLine": e.get("startLine"),
                "endLine": e.get("endLine"), "sentenceIndex": e.get("sentenceIndex"),
                "decisionStatus": e.get("decisionStatus"), "decisionDate": e.get("decisionDate"),
                "contentHash": e.get("contentHash") or sha256_hex(e.get("text") or e["name"]),
                "aliases": e.get("aliases"), "inferred": e.get("inferred"),
                "references": canonical_json(stored[key]).decode() if key in stored else None,
            })  # fmt: skip
        ids = [stable_id("el", vid, k) for k in unique]
        parents = [{"s": stable_id("el", vid, e["parent"]), "t": stable_id("el", vid, k)}
                   for k, e in unique.items() if e.get("parent") in unique]  # fmt: skip

        async def work(tx: AsyncManagedTransaction) -> None:
            for label, batch in by_label.items():
                await rows(tx, ELEMENT_QUERIES[label], vid=vid, rid=rid, pid=pid, els=batch)
            await rows(
                tx,
                "MATCH (:ArtifactVersion {id: $vid})-[h:HAS_ELEMENT]->(old:Element) WHERE NOT old.id IN $ids DELETE h",
                vid=vid, ids=ids,
            )  # fmt: skip
            await rows(tx, RELATION_QUERIES["CONTAINS"], rels=parents)

        await self.service.kg.write_tx(work)
        return len(unique)

    async def _write_links(
        self, pid: str, vid: str, rid: str, links: list[dict[str, Any]], rels: dict[str, list[dict[str, str]]]
    ) -> int:
        """Deactivate the version's explicit-reference links not found again, activate found ones."""

        async def work(tx: AsyncManagedTransaction) -> int:
            await rows(
                tx,
                "MATCH (:ArtifactVersion {id: $vid})-[:HAS_ELEMENT]->(:Element)<-[:TARGET]-"
                "(l:TraceLink {approach: $approach, active: true}) WHERE NOT l.id IN $keep SET l.active = false",
                vid=vid, approach=APPROACH, keep=[x["id"] for x in links],
            )  # fmt: skip
            created = await self._activate(tx, pid, rid, links)
            for rel, batch in rels.items():
                await rows(tx, RELATION_QUERIES[rel], rels=batch)
            return created

        return await self.service.kg.write_tx(work)

    async def _activate(self, tx: AsyncManagedTransaction, pid: str, rid: str, links: list[dict[str, Any]]) -> int:
        created = await rows(
            tx,
            "MATCH (r:Run {id: $rid}) UNWIND $links AS x "
            "MATCH (s:Element {id: x.source}), (t:Element {id: x.target}) "
            "MERGE (l:TraceLink {id: x.id}) ON CREATE SET l.projectId = $pid, l.linkType = x.linkType, "
            "l.confidence = 1.0, l.approach = $approach, l.approachVersion = $version, l.configHash = $configHash, "
            "l.status = 'candidate', l.runId = $rid, l.createdAt = $now "
            "WITH r, s, t, l, coalesce(l.active, false) AS was "
            "SET l.active = true MERGE (l)-[:SOURCE]->(s) MERGE (l)-[:TARGET]->(t) MERGE (r)-[:PRODUCED]->(l) "
            "RETURN sum(CASE WHEN was THEN 0 ELSE 1 END) AS n",
            rid=rid, links=links, pid=pid, approach=APPROACH, version=APPROACH_VERSION,
            configHash=self.service.explicit_hash, now=now(),
        )  # fmt: skip
        return int(created[0]["n"]) if created else 0

    async def _re_resolve(self, pid: str, rid: str) -> int:
        """Explicit links of the whole project against the current Requirements (R-014); writes only the difference."""
        kg, h = self.service.kg, self.service.explicit_hash
        targets: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for r in await kg.read(
            "MATCH (:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v)-[:HAS_ELEMENT]->(e:Requirement) "
            "RETURN e.id AS id, e.key AS key ORDER BY e.id",
            p=pid,
        ):  # fmt: skip
            targets[r["key"].split("#", 1)[1]].append((r["id"], r["key"]))
        desired: dict[str, dict[str, Any]] = {}
        for e in await kg.read(
            "MATCH (:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v)-[:HAS_ELEMENT]->(e:Element) "
            "WHERE e.references IS NOT NULL RETURN e.id AS id, e.key AS key, e.references AS refs ORDER BY e.id",
            p=pid,
        ):  # fmt: skip
            for ref in json.loads(e["refs"]):
                for referenced, key in targets.get(ref["text"], []):
                    if referenced != e["id"] and key != e["key"]:
                        lid = stable_id("tl", APPROACH, APPROACH_VERSION, h, referenced, e["id"])
                        desired[lid] = {"id": lid, "source": referenced, "target": e["id"], "linkType": ref["linkType"]}
        active = {
            r["id"]
            for r in await kg.read(
                "MATCH (l:TraceLink {projectId: $p, approach: $a, active: true})-[:TARGET]->(e:Element) "
                "WHERE e.references IS NOT NULL RETURN l.id AS id ORDER BY l.id",
                p=pid,
                a=APPROACH,
            )  # fmt: skip
        }
        stale = sorted(active - desired.keys())
        missing = [desired[i] for i in sorted(desired.keys() - active)]
        if not stale and not missing:
            return 0

        async def work(tx: AsyncManagedTransaction) -> int:
            for i in range(0, len(stale), LINK_BATCH):
                await rows(
                    tx, "MATCH (l:TraceLink) WHERE l.id IN $ids SET l.active = false", ids=stale[i : i + LINK_BATCH]
                )
            return sum([await self._activate(tx, pid, rid, missing[i : i + LINK_BATCH])
                        for i in range(0, len(missing), LINK_BATCH)])  # fmt: skip

        return await kg.write_tx(work)
