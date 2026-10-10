"""Rule configuration (config/orchestrator/rules.yaml) and the event -> step rules of Stage 1 §6."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sandman_agent import Contracts, Event, KgClient

EXTRACT, TLR, DETECT = "extract-artifact-versions", "recover-trace-links", "detect-inconsistencies"
SKILL_AGENT = {EXTRACT: "extraction-agent", TLR: "tlr-agent", DETECT: "inconsistency-agent"}
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
class Rules:
    debounce: float = 10.0
    max_per_agent: int = 2
    timeouts: Mapping[str, float] = field(default_factory=lambda: {EXTRACT: 900.0, TLR: 3600.0, DETECT: 1800.0})
    poll_initial: float = 30.0
    poll_max: float = 120.0
    max_attempts: int = 3
    backoff: tuple[float, ...] = (30.0, 120.0, 600.0)
    kind_pairs: tuple[KindPair, ...] = ()

    @classmethod
    def load(cls, path: Path, contracts: Contracts) -> "Rules":
        raw: dict[str, Any] = contracts.load_config("orchestrator-rules", path)
        poll, retry = raw.get("poll", {}), raw.get("retry", {})
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


class Graph(Protocol):
    """Read-only KG lookups (the orchestrator's only KG access)."""

    async def versions(self, project_id: str, version_ids: Sequence[str]) -> list[Version]: ...

    async def current(self, project_id: str) -> list[Version]: ...


class KgGraph:
    def __init__(self, kg: KgClient) -> None:
        self._kg = kg

    async def versions(self, project_id: str, version_ids: Sequence[str]) -> list[Version]:
        rows = await self._kg.read(
            "MATCH (a:Artifact {projectId: $p})-[:HAS_VERSION]->(v:ArtifactVersion) WHERE v.id IN $ids "
            "RETURN v.id AS id, a.id AS artifact, a.kind AS kind",
            p=project_id, ids=list(version_ids),
        )  # fmt: skip
        return [Version(r["id"], r["artifact"], r["kind"]) for r in rows]

    async def current(self, project_id: str) -> list[Version]:
        rows = await self._kg.read(
            "MATCH (a:Artifact {projectId: $p})-[:CURRENT_VERSION]->(v:ArtifactVersion) "
            "RETURN v.id AS id, a.id AS artifact, a.kind AS kind",
            p=project_id,
        )
        return [Version(r["id"], r["artifact"], r["kind"]) for r in rows]


def ids(values: Any) -> list[str]:
    return sorted(set(values))


def detect(project_id: str, artifact_ids: Any) -> StepSpec:
    return StepSpec(DETECT, {"projectId": project_id, "scopeArtifactIds": ids(artifact_ids)}, mergeable=True)


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
        return specs
    if event.type == "sandman.extraction.completed":
        changed = await graph.versions(project, d["artifactVersionIds"])
        specs = tlr_steps(project, changed, await graph.current(project), rules) if rules.kind_pairs else []
        linked = {i for s in specs for k in ("sourceVersionIds", "targetVersionIds") for i in s.input[k]}
        unlinked = [v.artifact_id for v in changed if v.id not in linked]
        return [*specs, detect(project, unlinked)] if unlinked else specs
    if event.type == "sandman.tracelinks.updated":
        versions = await graph.versions(project, [*d["sourceVersionIds"], *d["targetVersionIds"]])
        return [detect(project, (v.artifact_id for v in versions))] if versions else []
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
    """Steps of a manual workflow; 'full' starts with extraction and chains via the event rules."""
    if kind == "detect":
        return [StepSpec(DETECT, {"projectId": project_id, **params})]
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
