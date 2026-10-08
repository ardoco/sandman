"""Skill extract-artifact-versions: stub extraction, explicit references, plugin routing (Stage 2 §4.2)."""

import asyncio
import logging
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, LiteralString

import httpx
from neo4j import AsyncManagedTransaction
from sandman_agent import A2aPluginClient, PluginError, RunRecorder, SkillContext, SkillError, SkillResult, stable_id
from sandman_agent.ids import canonical_json, sha256_hex
from sandman_agent.kg import now

from sandman_extraction import builtin, documents, models
from sandman_extraction.bounds import add_warnings
from sandman_extraction.ingest import rows
from sandman_extraction.stub import stub_elements

if TYPE_CHECKING:
    from sandman_extraction.app import Service

log = logging.getLogger(__name__)

SKILL = "extract-artifact-versions"
TOOL, TOOL_VERSION = "stub-extractor", "0"
APPROACH, APPROACH_VERSION = "explicit-reference", "0"


def _element_query(label: LiteralString) -> LiteralString:
    return (
        "MATCH (v:ArtifactVersion {id: $vid}), (r:Run {id: $rid}) UNWIND $els AS e "
        "MERGE (n:Element {id: e.id}) ON CREATE SET n.runId = $rid "
        "SET n:" + label + " "
        "SET n.key = e.key, n.projectId = $pid, n.kind = e.kind, n.name = e.name, n.qualifiedName = e.qualifiedName, "
        "n.text = e.text, n.sentenceIndex = e.sentenceIndex, n.startLine = e.startLine, "
        "n.endLine = e.endLine, n.contentHash = e.contentHash "
        "MERGE (v)-[:HAS_ELEMENT]->(n) MERGE (r)-[:PRODUCED]->(n)"
    )


def _relation_query(rel: LiteralString) -> LiteralString:
    return "UNWIND $rels AS x MATCH (s:Element {id: x.s}), (t:Element {id: x.t}) MERGE (s)-[:" + rel + "]->(t)"


LABELS: tuple[LiteralString, ...] = ("Requirement", "TextUnit", "ModelElement", "CodeUnit", "TestCase")
RELATIONS: tuple[LiteralString, ...] = ("CONTAINS", "DEPENDS_ON", "PROVIDES", "REQUIRES")
ELEMENT_QUERIES = {label: _element_query(label) for label in LABELS}
RELATION_QUERIES = {rel: _relation_query(rel) for rel in RELATIONS}


def match_references(content: bytes, elements: list[dict[str, Any]], patterns: list[str]) -> list[dict[str, Any]]:
    """Regex matches as references from the innermost element containing the match line."""
    text = content.decode("utf-8", errors="replace")
    refs = []
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            line = text.count("\n", 0, m.start()) + 1
            around = [e for e in elements if e["startLine"] <= line <= e["endLine"]]
            source = min(around, key=lambda e: e["endLine"] - e["startLine"]) if around else elements[0]
            refs.append({"source": source["localKey"], "text": m[0], "linkType": "relates"})
    return refs


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
            "RETURN v.id AS id, a.id AS art, a.path AS path, a.kind AS kind, a.format AS format, v.storageUri AS uri",
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
                runs, run_id, common, input_key, pid, project[0]["llmPolicy"], plugins, by_id, use_builtin
            )
        except Exception as e:
            await runs.fail(run_id, f"{type(e).__name__}: {e}")
            raise
        await runs.complete(run_id, status=output["status"])
        event = {k: output[k] for k in ("runId", "status", "elementCount", "explicitLinkCount", "pluginRuns")}
        event |= {"projectId": pid, "artifactVersionIds": vids}
        return SkillResult(output, "sandman.extraction.completed", event)

    async def _extract(
        self,
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
        elements: dict[str, list[dict[str, Any]]] = {}  # version id -> elements
        refs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        relations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        producer: dict[str, str] = {}
        summaries, warnings, status = [], [], "completed"
        current = await svc.kg.read(
            "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v) "
            "RETURN a.path AS path, a.kind AS kind, a.format AS format, v.id AS id, v.storageUri AS uri",
            p=pid,
        )

        async def stub(v: dict[str, Any]) -> None:
            nonlocal status
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
            refs[v["id"]] = match_references(content, elements[v["id"]], patterns)
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

        meta = {v["id"]: v for vs in plugins.values() for v in vs}
        element_count = 0
        for vid, els in elements.items():
            element_count += await self._write_elements(pid, meta[vid]["art"], vid, producer[vid], els)
        # links and relations after all element sets, so references into this run's other versions resolve
        targets: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for r in await svc.kg.read(
            "MATCH (:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v)-[:HAS_ELEMENT]->(e:Requirement) "
            "RETURN e.id AS id, e.key AS key "
            "UNION MATCH (v:ArtifactVersion)-[:HAS_ELEMENT]->(e:Requirement) WHERE v.id IN $vids "
            "RETURN e.id AS id, e.key AS key",
            p=pid, vids=list(meta),
        ):  # fmt: skip
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
                        lid = stable_id("tl", APPROACH, APPROACH_VERSION, svc.config_hash, referenced, referrer)
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

    async def _write_elements(self, pid: str, art: str, vid: str, rid: str, els: list[dict[str, Any]]) -> int:
        """Replace the version's element set in one transaction."""
        unique: dict[str, dict[str, Any]] = {}
        for e in els:
            unique.setdefault(e["localKey"], e)
        by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for key, e in unique.items():
            by_label[e["label"]].append({
                "id": stable_id("el", vid, key), "key": f"{art}#{key}", "kind": e["kind"], "name": e["name"],
                "qualifiedName": e.get("qualifiedName"), "text": e.get("text"), "startLine": e.get("startLine"),
                "endLine": e.get("endLine"), "sentenceIndex": e.get("sentenceIndex"),
                "contentHash": sha256_hex(e.get("text") or e["name"]),
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
        """Deactivate the version's explicit-reference links, (re)activate the found ones, merge relations."""

        async def work(tx: AsyncManagedTransaction) -> int:
            await rows(
                tx,
                "MATCH (:ArtifactVersion {id: $vid})-[:HAS_ELEMENT]->(:Element)<-[:TARGET]-"
                "(l:TraceLink {approach: $approach, active: true}) SET l.active = false",
                vid=vid, approach=APPROACH,
            )  # fmt: skip
            created = await rows(
                tx,
                "MATCH (r:Run {id: $rid}) UNWIND $links AS x "
                "MATCH (s:Element {id: x.source}), (t:Element {id: x.target}) "
                "MERGE (l:TraceLink {id: x.id}) ON CREATE SET l.projectId = $pid, l.linkType = x.linkType, "
                "l.confidence = 1.0, l.approach = $approach, l.approachVersion = $version, l.configHash = $configHash, "
                "l.status = 'candidate', l.runId = $rid, l.createdAt = $now "
                "SET l.active = true MERGE (l)-[:SOURCE]->(s) MERGE (l)-[:TARGET]->(t) MERGE (r)-[:PRODUCED]->(l) "
                "RETURN count(l) AS n",
                rid=rid, links=links, pid=pid, approach=APPROACH, version=APPROACH_VERSION,
                configHash=self.service.config_hash, now=now(),
            )  # fmt: skip
            for rel, batch in rels.items():
                await rows(tx, RELATION_QUERIES[rel], rels=batch)
            return int(created[0]["n"]) if created else 0

        return await self.service.kg.write_tx(work)
