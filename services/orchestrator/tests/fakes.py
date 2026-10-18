"""Fake A2A agents (sandman_agent.a2a.serve) whose completion events go straight to the orchestrator."""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
from sandman_agent import A2aClient, Event, SkillContext, SkillError, SkillResult, serve
from sandman_agent.config import DEFAULT_CONTRACTS_DIR, Settings
from sandman_agent.contracts import Contracts
from sandman_agent.testing import InMemoryEventBus
from sandman_orchestrator.orchestrator import Orchestrator
from sandman_orchestrator.rules import (
    DERIVE,
    DETECT,
    EXTRACT,
    PRUNE_DATA,
    PRUNE_LINKS,
    TLR,
    KindPair,
    ModelDerivation,
    Retention,
    Rules,
    Version,
)

TOKENS = {"orchestrator": "orch-token", "query-service": "qs-token", "dashboard": "dash-token", "tlr-agent": "tlr"}
VERIFY = {name: hashlib.sha256(token.encode()).hexdigest() for name, token in TOKENS.items()}
AGENTS = {"extraction-agent": "http://extraction-agent:8000", "tlr-agent": "http://tlr-agent:8080",
          "inconsistency-agent": "http://inconsistency-agent:8080"}  # fmt: skip
CARDS = {EXTRACT: "extraction-agent", TLR: "tlr-agent", DETECT: "inconsistency-agent",
         PRUNE_LINKS: "tlr-agent", PRUNE_DATA: "extraction-agent", DERIVE: "extraction-agent"}  # fmt: skip
RULES = Rules(
    debounce=0,
    timeouts={EXTRACT: 5, TLR: 5, DETECT: 5, PRUNE_LINKS: 5, PRUNE_DATA: 5, DERIVE: 5},
    poll_initial=0.02,
    poll_max=0.05,
    backoff=(0.01,),
    kind_pairs=(KindPair("requirements", "code", ("stub-name-match",)),),
    retention=Retention(enabled=False),
    model_derivation=ModelDerivation(enabled=False),
)
RUN = "run_" + "0" * 32


def vid(n: int) -> str:
    return f"ver_{n:032x}"


def aid(n: int) -> str:
    return f"art_{n:032x}"


@dataclass
class FakeGraph:
    """Versions 1-2 requirements, 3-4 code, 5 other; artifact n for version n; all current."""

    known: list[Version] = field(
        default_factory=lambda: [
            Version(vid(n), aid(n), kind)
            for n, kind in ((1, "requirements"), (2, "requirements"), (3, "code"), (4, "code"), (5, "other"))
        ]  # fmt: skip
    )

    async def versions(self, project_id: str, version_ids: Sequence[str]) -> list[Version]:
        return [v for v in self.known if v.id in version_ids]

    async def current(self, project_id: str) -> list[Version]:
        return list(self.known)

    async def projects(self) -> list[str]:
        return ["mini"]


def output_and_event(skill: str, input: dict[str, Any]) -> SkillResult:
    p = input["projectId"]
    if skill == EXTRACT:
        out = {"runId": RUN, "status": "completed", "elementCount": 1, "explicitLinkCount": 0, "pluginRuns": []}
        event = {"projectId": p, "artifactVersionIds": input["artifactVersionIds"], **out}
        del event["status"]
        return SkillResult(out, "sandman.extraction.completed", {**event, "status": "completed"})
    if skill == TLR:
        runs = [{"approach": "stub", "approachVersion": "0", "runId": RUN, "status": "completed", "linkCount": 1}]
        event = {"projectId": p, "sourceVersionIds": input["sourceVersionIds"],
                 "targetVersionIds": input["targetVersionIds"], "runs": runs}  # fmt: skip
        return SkillResult({"runs": runs}, "sandman.tracelinks.updated", event)
    if skill in (PRUNE_LINKS, PRUNE_DATA):
        scope = "trace-links" if skill == PRUNE_LINKS else "artifact-data"
        keep, dry = input.get("keepVersionsPerArtifact", 20), input.get("dryRun", True)
        event = {"projectId": p, "scope": scope, "runId": RUN, "dryRun": dry, "keepVersionsPerArtifact": keep,
                 "removedCount": 0}  # fmt: skip
        if "protectedContentHashes" in input:
            event["protectedContentHashes"] = input["protectedContentHashes"]
        return SkillResult({"runId": RUN, "removedCount": 0, "dryRun": dry}, "sandman.retention.pruned", event)
    lists: dict[str, Any] = {"newIds": [], "reopenedIds": [], "autoResolvedIds": [], "stillOpenCount": 0}
    out = {"runId": RUN, "status": "completed", **lists, "detectorRuns": []}
    event = {"projectId": p, "runId": RUN, "status": "completed", **lists}
    if "scopeArtifactIds" in input:
        event["scopeArtifactIds"] = input["scopeArtifactIds"]
    return SkillResult(out, "sandman.inconsistencies.updated", event)


class FakeSkill:
    """Script per call: 'ok', 'hang' (until cancelled or released), 'talk' (activity, then hang) or SkillError."""

    def __init__(self, id: str) -> None:
        self.id = id
        self.script: list[str | SkillError] = []
        self.calls: list[SkillContext] = []
        self.release = asyncio.Event()

    @property
    def cancelled(self) -> int:
        return sum(c.cancelled.is_set() for c in self.calls)

    async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
        self.calls.append(ctx)
        action = self.script.pop(0) if self.script else "ok"
        if isinstance(action, SkillError):
            raise action
        if action == "talk":
            await ctx.progress("annotating sentences 1/3")
        if action in ("hang", "talk"):
            cancel = asyncio.create_task(ctx.cancelled.wait())
            await asyncio.wait([cancel, asyncio.create_task(self.release.wait())], return_when="FIRST_COMPLETED")
            if ctx.cancelled.is_set():
                raise asyncio.CancelledError
        return output_and_event(self.id, input)


class Router(httpx.AsyncBaseTransport):
    """Routes requests by host to the current app of each fake agent (apps can be replaced = restart)."""

    def __init__(self) -> None:
        self.apps: dict[str, Any] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await httpx.ASGITransport(app=self.apps[request.url.host]).handle_async_request(request)


class Bus(InMemoryEventBus):
    """Delivers published events to the orchestrator's consumer handler."""

    target: Callable[[Event, int], Awaitable[None]] | None = None

    async def publish(self, type: str, subject: str, data: Mapping[str, Any], **kw: Any) -> Event:
        event = await super().publish(type, subject, data, **kw)
        if self.target:
            await self.target(event, 1)
        return event


@dataclass
class Env:
    orch: Orchestrator
    skills: dict[str, FakeSkill]
    router: Router
    bus: Bus
    contracts: Contracts
    path: Path

    def with_rules(self, **changes: Any) -> Orchestrator:
        """Replace the orchestrator by one on the same DB with changed rules."""
        self.orch.close()
        return self.new_orchestrator(self.path, replace(RULES, **changes))

    def restart_agent(self, skill: str) -> None:
        """New app instance = empty in-memory task store."""
        name = CARDS[skill]
        settings = Settings(service_name=name, service_verify=VERIFY)
        card = DEFAULT_CONTRACTS_DIR / "agent-cards" / f"{name}.json"
        self.router.apps[name] = serve(
            card,
            [self.skills[k] for k, v in CARDS.items() if v == name],
            settings,
            events=self.bus,
            contracts=self.contracts,
        )

    def new_orchestrator(self, db: str | Path, rules: Rules = RULES) -> Orchestrator:
        a2a = A2aClient(TOKENS["orchestrator"], transport=self.router)
        self.orch = Orchestrator(db, rules, FakeGraph(), a2a, AGENTS, self.contracts)
        self.bus.target = self.orch.consume
        return self.orch

    def steps(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.orch.db.execute("SELECT * FROM steps ORDER BY rowid")]

    async def until(self, predicate: Callable[[], bool], timeout: float = 5) -> None:
        """Drive the executor until predicate holds."""
        async with asyncio.timeout(timeout):
            while not predicate():
                await self.orch.tick()
                await asyncio.sleep(0.01)


def event(type: str, data: dict[str, Any], causation: str | None = None, id: str | None = None) -> Event:
    return Event(id or str(uuid.uuid4()), type, "sandman/test", data["projectId"], "2026-10-07T00:00:00Z", data,
                 causation_id=causation)  # fmt: skip


def ingestion(
    created: list[str] = (), deleted: list[str] = (), reactivated: list[str] = (), actor: str = "user:alice", **kw: Any
) -> Event:  # type: ignore[assignment]
    data = {"projectId": "mini", "ingestionId": "ing_" + "a" * 32, "seq": 1, "mode": "full", "actor": actor,
            "createdVersionIds": list(created), "reactivatedVersionIds": list(reactivated),
            "deletedArtifactIds": list(deleted), "unchangedCount": 0}  # fmt: skip
    return event("sandman.ingestion.completed", data, **kw)
