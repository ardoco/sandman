"""Rule configuration (config/orchestrator/rules.yaml) and the event -> step rules of Stage 1 §6."""

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml
from sandman_agent import Contracts, Event, KgClient

log = logging.getLogger(__name__)

EXTRACT, TLR, DETECT = "extract-artifact-versions", "recover-trace-links", "detect-inconsistencies"
PRUNE_LINKS, PRUNE_DATA = "prune-trace-links", "prune-artifact-data"
DERIVE = "derive-architecture-model"
DERIVATION_ACTOR = "system:model-derivation"
DERIVE_INPUT_KINDS = ("architecture-doc", "architecture-model")
SKILL_AGENT = {
    EXTRACT: "extraction-agent",
    TLR: "tlr-agent",
    DETECT: "inconsistency-agent",
    PRUNE_LINKS: "tlr-agent",
    PRUNE_DATA: "extraction-agent",
    DERIVE: "extraction-agent",
}
ID_LISTS = ("artifactVersionIds", "sourceVersionIds", "targetVersionIds", "scopeArtifactIds")


def seconds(value: str | float) -> float:
    """'30s', '2m', '1h' or a number of seconds."""
    if isinstance(value, int | float):
        return float(value)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", value)
    if not m:
        raise ValueError(f"invalid duration {value!r}")
    return float(m[1]) * {"": 1, "s": 1, "m": 60, "h": 3600}[m[2]]


@dataclass(frozen=True)
class KindPair:
    source: str
    target: str
    approaches: tuple[str, ...] = ()


@dataclass(frozen=True)
class Retention:
    """Scheduled prune workflows (T5.2); per-project overrides in `projects`."""

    enabled: bool = True
    interval: float = 86400.0
    keep: int = 20
    dry_run: bool = True
    experiments_dir: Path = Path("/experiments")
    projects: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def project(self, project_id: str) -> tuple[bool, int, bool]:
        o = self.projects.get(project_id, {})
        return (
            bool(o.get("enabled", self.enabled)),
            int(o.get("keepVersionsPerArtifact", self.keep)),
            bool(o.get("dryRun", self.dry_run)),
        )


@dataclass(frozen=True)
class ModelDerivation:
    """Derived architecture models (T2.7); per-project overrides in `projects`."""

    enabled: bool = True
    projects: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def project(self, project_id: str) -> bool:
        return bool(self.projects.get(project_id, {}).get("enabled", self.enabled))


@dataclass(frozen=True)
class Rules:
    debounce: float = 10.0
    max_per_agent: int = 2
    timeouts: Mapping[str, float] = field(
        default_factory=lambda: {
            EXTRACT: 900.0,
            TLR: 3600.0,
            DETECT: 1800.0,
            PRUNE_LINKS: 1800.0,
            PRUNE_DATA: 1800.0,
            DERIVE: 1800.0,
        }
    )
    poll_initial: float = 30.0
    poll_max: float = 120.0
    max_attempts: int = 3
    backoff: tuple[float, ...] = (30.0, 120.0, 600.0)
    kind_pairs: tuple[KindPair, ...] = ()
    retention: Retention = field(default_factory=Retention)
    model_derivation: ModelDerivation = field(default_factory=ModelDerivation)

    @classmethod
    def load(cls, path: Path, contracts: Contracts) -> "Rules":
        raw: dict[str, Any] = contracts.load_config("orchestrator-rules", path)
        poll, retry, ret = raw.get("poll", {}), raw.get("retry", {}), raw.get("retention", {})
        derivation = raw.get("modelDerivation", {})
        default = Retention()
        return cls(
            debounce=seconds(raw.get("debounceSeconds", 10)),
            max_per_agent=int(raw.get("maxConcurrentPerAgent", 2)),
            timeouts={**cls().timeouts, **{k: seconds(v) for k, v in raw.get("timeouts", {}).items()}},
            poll_initial=seconds(poll.get("initial", 30)),
            poll_max=seconds(poll.get("max", 120)),
            max_attempts=int(retry.get("maxAttempts", 3)),
            backoff=tuple(seconds(b) for b in retry.get("backoff", (30, 120, 600))) or (30.0,),
            kind_pairs=tuple(
                KindPair(p["source"], p["target"], tuple(p.get("approaches", ())))
                for p in raw.get("tlr", {}).get("kindPairs", ())
            ),
            retention=Retention(
                enabled=ret.get("enabled", default.enabled),
                interval=seconds(ret.get("interval", default.interval)),
                keep=int(ret.get("keepVersionsPerArtifact", default.keep)),
                dry_run=ret.get("dryRun", default.dry_run),
                experiments_dir=Path(ret.get("experimentsDir", default.experiments_dir)),
                projects=ret.get("projects", {}),
            ),
            model_derivation=ModelDerivation(
                enabled=derivation.get("enabled", True), projects=derivation.get("projects", {})
            ),
        )


@dataclass(frozen=True)
class StepSpec:
    """A step to create; mergeable steps are debounced and coalesce with pending steps of the same shape."""

    skill: str
    input: dict[str, Any]
    mergeable: bool = False


@dataclass(frozen=True)
class Version:
    id: str
    artifact_id: str
    kind: str
    origin: str = "user"


class Graph(Protocol):
    """Read-only KG lookups (the orchestrator's only KG access)."""

    async def versions(self, project_id: str, version_ids: Sequence[str]) -> list[Version]: ...

    async def current(self, project_id: str) -> list[Version]: ...

    async def projects(self) -> list[str]: ...


class KgGraph:
    def __init__(self, kg: KgClient) -> None:
        self._kg = kg

    async def versions(self, project_id: str, version_ids: Sequence[str]) -> list[Version]:
        rows = await self._kg.read(
            "MATCH (a:Artifact {projectId: $p})-[:HAS_VERSION]->(v:ArtifactVersion) WHERE v.id IN $ids "
            "RETURN v.id AS id, a.id AS artifact, a.kind AS kind, coalesce(a.origin, 'user') AS origin",
            p=project_id, ids=list(version_ids),
        )  # fmt: skip
        return [Version(r["id"], r["artifact"], r["kind"], r["origin"]) for r in rows]

    async def current(self, project_id: str) -> list[Version]:
        rows = await self._kg.read(
            "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v:ArtifactVersion) "
            "RETURN v.id AS id, a.id AS artifact, a.kind AS kind, coalesce(a.origin, 'user') AS origin",
            p=project_id,
        )
        return [Version(r["id"], r["artifact"], r["kind"], r["origin"]) for r in rows]

    async def projects(self) -> list[str]:
        rows = await self._kg.read("MATCH (p:Project) RETURN p.id AS id ORDER BY p.id")
        return [r["id"] for r in rows]


def ids(values: Any) -> list[str]:
    return sorted(set(values))


def protected_hashes(directory: Path) -> list[str]:
    """Content hashes of the snapshot in every experiments/**/experiment.yaml; an unreadable manifest raises."""
    if not directory.is_dir():
        log.warning(
            "experiments directory missing, no version is protected by an experiment",
            extra={"fields": {"dir": str(directory)}},
        )
        return []
    found: set[str] = set()
    for manifest in directory.rglob("experiment.yaml"):
        artifacts = ((yaml.safe_load(manifest.read_text()) or {}).get("snapshot") or {}).get("artifacts") or {}
        found.update(h for h in artifacts.values() if isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h))
    return sorted(found)


def detect(project_id: str, artifact_ids: Any) -> StepSpec:
    return StepSpec(DETECT, {"projectId": project_id, "scopeArtifactIds": ids(artifact_ids)}, mergeable=True)


def derive(project_id: str) -> StepSpec:
    return StepSpec(DERIVE, {"projectId": project_id}, mergeable=True)


def tlr_steps(project_id: str, changed: list[Version], current: list[Version], rules: Rules) -> list[StepSpec]:
    """changed x counterpart current versions per kind pair; changed=current links everything."""
    specs = []
    for pair in rules.kind_pairs:
        for side_changed, side_current, changed_is_source in ((pair.source, pair.target, True),
                                                              (pair.target, pair.source, False)):  # fmt: skip
            mine = ids(v.id for v in changed if v.kind == side_changed)
            other = ids(v.id for v in current if v.kind == side_current)
            if not mine or not other:
                continue
            src, tgt = (mine, other) if changed_is_source else (other, mine)
            input: dict[str, Any] = {"projectId": project_id, "sourceVersionIds": src, "targetVersionIds": tgt}
            if pair.approaches:
                input["approaches"] = list(pair.approaches)
            specs.append(StepSpec(TLR, input, mergeable=True))
    return specs


async def plan(event: Event, graph: Graph, rules: Rules) -> list[StepSpec]:
    """Follow-up steps of one event (Stage 1 §6 rule table)."""
    d, project = event.data, str(event.data["projectId"])
    if event.type == "sandman.ingestion.completed":
        specs = []
        versions = [*d["createdVersionIds"], *d["reactivatedVersionIds"]]
        if versions:
            specs.append(StepSpec(EXTRACT, {"projectId": project, "artifactVersionIds": ids(versions)}))
        if d["deletedArtifactIds"]:  # also for mixed ingestions, so deleted artifacts resolve their findings
            specs.append(detect(project, d["deletedArtifactIds"]))
            if d["actor"] != DERIVATION_ACTOR and rules.model_derivation.project(project):
                specs.append(derive(project))
        return specs
    if event.type == "sandman.extraction.completed":
        changed = await graph.versions(project, d["artifactVersionIds"])
        specs = tlr_steps(project, changed, await graph.current(project), rules) if rules.kind_pairs else []
        linked = {i for s in specs for k in ("sourceVersionIds", "targetVersionIds") for i in s.input[k]}
        unlinked = [v.artifact_id for v in changed if v.id not in linked]
        specs = [*specs, detect(project, unlinked)] if unlinked else specs
        user_input = any(v.kind in DERIVE_INPUT_KINDS and v.origin != "derived" for v in changed)
        return [*specs, derive(project)] if user_input and rules.model_derivation.project(project) else specs
    if event.type == "sandman.tracelinks.updated":
        versions = await graph.versions(project, [*d["sourceVersionIds"], *d["targetVersionIds"]])
        return [detect(project, (v.artifact_id for v in versions))] if versions else []
    if event.type == "sandman.retention.pruned" and d["scope"] == "trace-links":
        keep = {k: d[k] for k in ("keepVersionsPerArtifact", "dryRun", "protectedContentHashes") if k in d}
        return [StepSpec(PRUNE_DATA, {"projectId": project, **keep})]
    return []  # inconsistencies.updated closes the workflow; status.changed is audit only


def merge(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any] | None:
    """Union of the id lists if all other members are equal; absent scope = whole project."""
    if {k: v for k, v in a.items() if k not in ID_LISTS} != {k: v for k, v in b.items() if k not in ID_LISTS}:
        return None
    out = dict(a)
    for k in ID_LISTS:
        if k in a and k in b:
            out[k] = ids([*a[k], *b[k]])
        else:
            out.pop(k, None)
    return out


async def manual(kind: str, project_id: str, params: Mapping[str, Any], graph: Graph, rules: Rules) -> list[StepSpec]:
    """Steps of a manual workflow; 'full' and 'prune' chain via the event rules."""
    if kind == "detect":
        return [StepSpec(DETECT, {"projectId": project_id, **params})]
    if kind == "derive":
        return [StepSpec(DERIVE, {"projectId": project_id})]
    if kind == "prune":
        _, keep, dry_run = rules.retention.project(project_id)
        hashes = protected_hashes(rules.retention.experiments_dir)
        base: dict[str, Any] = {"keepVersionsPerArtifact": keep, "dryRun": dry_run}
        if hashes:
            base["protectedContentHashes"] = hashes
        return [StepSpec(PRUNE_LINKS, {"projectId": project_id, **base, **params})]
    current = await graph.current(project_id)
    if kind in ("extract", "full"):
        versions = ids(v.id for v in current)
        return (
            [StepSpec(EXTRACT, {"projectId": project_id, "artifactVersionIds": versions, **params})] if versions else []
        )
    specs = []
    for pair in rules.kind_pairs:  # tlr: all current versions per kind pair
        src = ids(v.id for v in current if v.kind == pair.source)
        tgt = ids(v.id for v in current if v.kind == pair.target)
        if src and tgt:
            base = {"approaches": list(pair.approaches)} if pair.approaches else {}
            input = {"projectId": project_id, "sourceVersionIds": src, "targetVersionIds": tgt, **base, **params}
            specs.append(StepSpec(TLR, input))
    return specs
