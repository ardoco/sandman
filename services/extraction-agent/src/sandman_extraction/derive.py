"""Skill derive-architecture-model: component names from architecture documentation, ExArch port (T2.7).

Port of ExArch from ArDoCo 2.1.0 (MIT): two prompts as two user turns and the name post-processing of the Java
informant. Documented deviations: names with `::` or a line break are dropped, an empty answer is an error.
"""

import logging
import unicodedata
from dataclasses import dataclass, field
from importlib import resources
from typing import TYPE_CHECKING, Any

from sandman_agent import RunRecorder, SkillContext, SkillError, SkillResult, stable_id
from sandman_agent.ids import canonical_json, sha256_hex
from sandman_agent.llm import BudgetExceededError, ChatMessage, ChatRequest, PolicyError, Usage

from sandman_extraction import ingest
from sandman_extraction.bounds import add_warnings
from sandman_extraction.text import render_sentences

if TYPE_CHECKING:
    from sandman_extraction.app import Service

log = logging.getLogger(__name__)

SKILL = "derive-architecture-model"
TOOL, TOOL_VERSION = "exarch", "2.1.0+x1"
ACTOR = "system:model-derivation"
PREFIX = ".sandman/derived/exarch/"
NO_ROUTE = "model derivation skipped: no LLM route for policy"
JAVA_WHITESPACE = frozenset(
    map(
        chr,
        [
            *range(0x09, 0x0E),
            *range(0x1C, 0x21),
            0x1680,
            *range(0x2000, 0x2007),
            *range(0x2008, 0x200B),
            0x2028,
            0x2029,
            0x205F,
            0x3000,
        ],
    )
)
JAVA_TRIM = "".join(map(chr, range(0x21)))


@dataclass(frozen=True)
class DeriveConfig:
    """config/extraction/model-derivation.yaml."""

    max_input_chars: int = 120_000
    max_tokens: int = 1024
    seed: int = 42
    max_components: int = 100
    max_name_length: int = 80

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DeriveConfig":
        """Missing keys keep their defaults."""
        d = cls()
        return cls(
            max_input_chars=int(raw.get("maxInputChars", d.max_input_chars)),
            max_tokens=int(raw.get("maxTokens", d.max_tokens)),
            seed=int(raw.get("seed", d.seed)),
            max_components=int(raw.get("maxComponents", d.max_components)),
            max_name_length=int(raw.get("maxNameLength", d.max_name_length)),
        )


def _template(name: str) -> str:
    return (resources.files("sandman_extraction") / "prompts" / f"{name}.txt").read_text(encoding="utf-8")


PROMPTS = [_template("exarch-doc-1"), _template("exarch-doc-2")]
PROMPT_IDS = [f"prompts/exarch-doc-{i}.txt@{sha256_hex(t)[:12]}" for i, t in enumerate(PROMPTS, 1)]


def _camel_case(name: str) -> str:
    if " " not in name:
        return name
    return "".join(w[0].upper() + w[1:].lower() for w in name.split(" ") if w)


def parse_names(answer: str) -> list[str]:
    """Names of the lines `- name`; blank lines and other lines are skipped (Java parseComponentNames)."""
    names = []
    for line in answer.split("\n"):
        if all(c in JAVA_WHITESPACE for c in line):
            continue
        line = line.strip(JAVA_TRIM)
        if line.startswith("-"):
            names.append(line[1:].strip(JAVA_TRIM))
    return names


def postprocess(answer: str) -> tuple[list[str], list[str]]:
    """(names sorted by UTF-16 code units, dropped names): Java post-processing without names `::` or a line break."""
    names, dropped = set(), []
    for raw in parse_names(answer):
        name = _camel_case(raw.replace("Components", "").replace("Component", ""))
        if all(c in JAVA_WHITESPACE for c in name):
            continue
        if "::" in name or "\n" in name or "\r" in name:
            dropped.append(name)
        else:
            names.add(name)
    return sorted(names, key=lambda n: n.encode("utf-16-be", errors="surrogatepass")), sorted(set(dropped))


def validate(names: list[str], cfg: DeriveConfig) -> tuple[list[str], list[str]]:
    """(kept names, warnings): drops overlong names and names with control characters, caps the count."""
    kept, warnings = [], []
    for name in names:
        if len(name) > cfg.max_name_length:
            reason = f"longer than {cfg.max_name_length} characters"
        elif any(unicodedata.category(c) in ("Cc", "Cs") for c in name):
            reason = "contains control characters"
        else:
            kept.append(name)
            continue
        log.warning("derived component name dropped", extra={"fields": {"reason": reason}})
        warnings.append(f"component name dropped ({reason}): {name[:40]!r}")
    if len(kept) > cfg.max_components:
        warnings.append(f"model derivation kept the first {cfg.max_components} of {len(kept)} component names")
        kept = kept[: cfg.max_components]
    return kept, warnings


def listing(names: list[str]) -> bytes:
    """Component listing: one line `Name::Name` per component, UTF-8, `\\n` endings."""
    return "".join(f"{n}::{n}\n" for n in names).encode("utf-8")


@dataclass
class Item:
    """One listing to write: the source version, the producing run and the content (None when reused)."""

    source: str
    run_id: str
    digest: str
    new_run: bool = False
    usage: Usage = Usage()
    resolved: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)


class DeriveSkill:
    """derive-architecture-model; idempotent: one derived listing per current architecture-doc version."""

    id = SKILL

    def __init__(self, service: "Service") -> None:
        self.service = service

    async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
        svc, pid = self.service, input["projectId"]
        project = await svc.kg.read("MATCH (p:Project {id: $p}) RETURN p.llmPolicy AS policy", p=pid)
        if not project:
            raise SkillError("INVALID_INPUT", f"unknown project {pid}", False)
        arts = await svc.kg.read(
            "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v) "
            "RETURN a.id AS id, a.path AS path, a.kind AS kind, a.origin AS origin, v.id AS vid, "
            "v.derivedFrom AS source ORDER BY a.path",
            p=pid,
        )
        derived = [a for a in arts if a["origin"] == "derived"]
        warnings: list[str] = []
        items: dict[str, Item] = {}
        blobs: dict[str, bytes] = {}
        runs = RunRecorder(svc.kg, svc.settings.service_name, svc.status.version)
        if any(a["kind"] == "architecture-model" and a["origin"] != "derived" for a in arts):
            retire = [a["id"] for a in derived]
        else:
            alive = {
                r["vid"]: r["alive"]
                for r in await svc.kg.read(
                    "UNWIND $ids AS vid MATCH (v:ArtifactVersion {id: vid})<-[:HAS_VERSION]-(sa:Artifact) "
                    "OPTIONAL MATCH (sa)-[:CURRENT_VERSION]->(c) RETURN vid, c.id AS alive",
                    ids=sorted({a["source"] for a in derived if a["source"]}),
                )
            }
            retire = [a["id"] for a in derived if alive.get(a["source"]) is None]
            have = {a["path"]: a["source"] for a in derived if a["id"] not in retire}
            todo = [
                d
                for d in arts
                if d["kind"] == "architecture-doc"
                and d["origin"] != "derived"
                and have.get(f"{PREFIX}{d['path']}.components") != d["vid"]
            ]
            await self._derive(ctx, runs, pid, project[0]["policy"], todo, items, blobs, warnings)
        output: dict[str, Any] = {"derivedVersionIds": [], "retiredArtifactIds": sorted(retire), "warnings": []}
        try:
            manifest = ingest.Manifest(
                mode="partial",
                kindRules=[ingest.KindRule(glob=".sandman/**", kind="architecture-model", format="component-listing")],
                files=[ingest.ManifestFile(path=p, sha256=i.digest) for p, i in items.items()],
            )
            derivation = {p: (i.source, i.run_id) for p, i in items.items()}
            ing_id = await ingest.ingest(svc.kg, svc.store, pid, manifest, blobs, ACTOR, derivation, retire)
            ing = await ingest.load_ingestion(svc.kg, pid, ing_id)
            assert ing is not None
        except Exception as e:
            for i in items.values():
                if i.new_run:
                    await runs.fail(i.run_id, f"{type(e).__name__}: {e}")
            raise
        for i in items.values():
            if i.new_run:
                await runs.complete(i.run_id, i.usage, "completed", i.resolved[0], len(set(i.resolved)) > 1)
        output["derivedVersionIds"] = sorted(
            stable_id("ver", stable_id("art", pid, p), i.digest) for p, i in items.items()
        )
        add_warnings(output["warnings"], warnings + [w for i in items.values() for w in i.warnings])
        data = {k: v for k, v in ingest.response(ing).items() if k != "createdAt"} | {"actor": ing["actor"]}
        await svc.kg.write("MATCH (g:Ingestion {id: $id}) SET g.eventPublished = true", id=ing_id)
        return SkillResult(output, ingest.INGESTION_COMPLETED, data)

    def _client(self, policy: str, route: str) -> Any | None:
        """The client of the policy, or None if its route or key is missing; never a cloud client for local-only."""
        svc = self.service
        if not svc.llm_configured:
            return None
        if svc.llm_factory is None and svc.selector is not None and route not in svc.selector.routes:
            return None
        try:
            return svc.llm_client(policy)
        except PolicyError:
            return None

    async def _reusable(self, pid: str, key: str, route: str, params: str, source: str) -> dict[str, Any] | None:
        found = await self.service.kg.read(
            "MATCH (r:Run {projectId: $p, tool: $tool, inputKey: $key, status: 'completed', model: $route, "
            "params: $params}) "
            "MATCH (v:ArtifactVersion {projectId: $p, derivedFrom: $src, derivationRunId: r.id}) "
            "RETURN r.id AS run, v.contentHash AS digest ORDER BY r.startedAt DESC, r.id LIMIT 1",
            p=pid, tool=TOOL, key=key, route=route, params=params, src=source,
        )  # fmt: skip
        return found[0] if found else None

    async def _sentences(self, version: str) -> str:
        found = await self.service.kg.read(
            "MATCH (:ArtifactVersion {id: $v})-[:HAS_ELEMENT]->(e:TextUnit) WHERE e.sentenceIndex IS NOT NULL "
            "RETURN e.text AS text, e.sentenceIndex AS sentenceIndex",
            v=version,
        )
        return render_sentences(found)

    async def _derive(
        self,
        ctx: SkillContext,
        runs: RunRecorder,
        pid: str,
        policy: str,
        docs: list[dict[str, Any]],
        items: dict[str, Item],
        blobs: dict[str, bytes],
        warnings: list[str],
    ) -> None:
        svc, cfg = self.service, self.service.derivation
        route = svc.chat_route(policy)
        params = canonical_json(
            {
                "temperature": 0.0, "seed": cfg.seed, "model": svc.route_model(route), "maxTokens": cfg.max_tokens,
                "toolVersion": TOOL_VERSION,
            }
        ).decode()  # fmt: skip
        client, skipped = None, False
        for doc in docs:
            path = f"{PREFIX}{doc['path']}.components"
            key = sha256_hex(
                canonical_json(
                    {
                        "input": {"projectId": pid, "versionId": doc["vid"]},
                        "tool": TOOL,
                        "configHash": svc.derivation_hash,
                        "promptIds": PROMPT_IDS,
                    }
                )
            )
            if old := await self._reusable(pid, key, route, params, doc["vid"]):
                items[path] = Item(doc["vid"], old["run"], old["digest"])
                continue
            if client is None and not skipped:
                client = self._client(policy, route)
                skipped = client is None
                if skipped:
                    warnings.append(NO_ROUTE)
            if client is None:
                continue
            text = await self._sentences(doc["vid"])
            if not text.strip():
                warnings.append(f"model derivation skipped for {doc['path']}: no extracted text")
                continue
            if len(text) > cfg.max_input_chars:
                warnings.append(f"model derivation skipped for {doc['path']}: text longer than {cfg.max_input_chars}")
                continue
            await ctx.progress(f"deriving components from {doc['path']}")
            await runs.cleanup_stale(key)
            run_id = await runs.start(
                project_id=pid, skill=SKILL, tool=TOOL, tool_version=TOOL_VERSION, input_key=key, model=route,
                params=params, config_hash=svc.derivation_hash, used_version_ids=[doc["vid"]],
                workflow_id=ctx.workflow_id, task_id=ctx.task_id, triggered_by=ctx.actor or f"service:{ctx.caller}",
                prompt_ids=PROMPT_IDS,
            )  # fmt: skip
            try:
                answer, usage, resolved = await self._ask(client, route, cfg, run_id, text)
                dropped_by_java_rules = postprocess(answer)
                names, found_warnings = validate(dropped_by_java_rules[0], cfg)
                for name in dropped_by_java_rules[1]:
                    log.warning("derived component name dropped", extra={"fields": {"reason": "::/line break"}})
                    found_warnings.append(f"component name dropped (contains '::' or a line break): {name[:40]!r}")
                if not names:
                    raise ValueError("answer holds no component names")
            except BudgetExceededError as e:
                await runs.fail(run_id, f"BudgetExceededError: {e}")
                raise
            except Exception as e:  # noqa: BLE001 - derivation is optional, any gateway failure keeps the ingestion
                await runs.fail(run_id, f"{type(e).__name__}: {e}")
                warnings.append(f"model derivation failed for {doc['path']}: {type(e).__name__}: {e}")
                continue
            content = listing(names)
            blobs[path] = content
            items[path] = Item(doc["vid"], run_id, sha256_hex(content), True, usage, resolved, found_warnings)

    async def _ask(
        self, client: Any, route: str, cfg: DeriveConfig, run_id: str, text: str
    ) -> tuple[str, Usage, tuple[str, ...]]:
        messages = [ChatMessage("user", PROMPTS[0].replace("%s", text, 1))]
        usage, resolved = Usage(), []
        for turn in range(2):
            response = await client.chat(
                ChatRequest(model=route, messages=list(messages), temperature=0.0, seed=cfg.seed,
                            max_tokens=cfg.max_tokens, user=run_id)
            )  # fmt: skip
            usage = Usage(usage.input_tokens + response.usage.input_tokens,
                          usage.output_tokens + response.usage.output_tokens)  # fmt: skip
            resolved.append(response.resolved_model)
            if response.finish_reason == "length":
                raise ValueError("response truncated")
            if turn == 0:
                messages += [response.message, ChatMessage("user", PROMPTS[1])]
        return response.message.content, usage, tuple(resolved)
