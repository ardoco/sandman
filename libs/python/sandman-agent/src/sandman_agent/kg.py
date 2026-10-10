"""Knowledge-graph access (Neo4j driver over Bolt to Memgraph) and run provenance."""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, LiteralString, TypeVar, cast

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction
from neo4j.exceptions import Neo4jError

from sandman_agent import telemetry
from sandman_agent.config import llm_mode
from sandman_agent.ids import new_run_id
from sandman_agent.llm import Usage

T = TypeVar("T")


class MemoryLimitError(Exception):
    """Memgraph aborted the query at its memory limit; not retried (Memgraph reports it as transient, spike T0.4)."""


def _no_retry_on_memory_limit[R](fn: Callable[[AsyncManagedTransaction], Awaitable[R]]) -> Callable[..., Awaitable[R]]:
    # the driver retries TransientError; re-raising as a plain exception stops the retry loop
    async def guarded(tx: AsyncManagedTransaction) -> R:
        try:
            return await fn(tx)
        except Neo4jError as e:
            if "memory limit exceeded" in (e.message or "").lower():
                raise MemoryLimitError(e.message) from e
            raise

    return guarded


def now() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class KgClient:
    """read() runs in a read transaction (Memgraph rejects writes there); write() in a write transaction."""

    def __init__(self, uri: str, user: str | None, password: str | None, driver: AsyncDriver | None = None) -> None:
        auth = (user, password or "") if user else None
        self._driver = driver or AsyncGraphDatabase.driver(uri, auth=auth)

    async def close(self) -> None:
        """Close the driver."""
        await self._driver.close()

    async def read(self, cypher: LiteralString, **params: Any) -> list[dict[str, Any]]:
        """Run a read query; rows as dicts.

        Rule: a query whose rows enter an LLM prompt or set the order of LLM calls must end in ORDER BY on a stable
        key (e.g. e.id); Memgraph returns unordered rows in no guaranteed order, which breaks replay determinism.
        """

        async def work(tx: AsyncManagedTransaction) -> list[dict[str, Any]]:
            return [r.data() async for r in await tx.run(cypher, params)]

        with telemetry.kg_span("read", cypher):
            async with self._driver.session() as session:
                return await session.execute_read(_no_retry_on_memory_limit(work))

    async def write(self, cypher: LiteralString, **params: Any) -> list[dict[str, Any]]:
        """Run a write query; transient errors are retried by the driver."""

        async def work(tx: AsyncManagedTransaction) -> list[dict[str, Any]]:
            return [r.data() async for r in await tx.run(cypher, params)]

        with telemetry.kg_span("write", cypher):
            async with self._driver.session() as session:
                return await session.execute_write(_no_retry_on_memory_limit(work))

    async def write_tx(self, fn: Callable[[AsyncManagedTransaction], Awaitable[T]]) -> T:
        """Run several statements in one write transaction."""
        with telemetry.kg_span("write_tx"):
            async with self._driver.session() as session:
                return cast(T, await session.execute_write(_no_retry_on_memory_limit(fn)))

    async def run_autocommit(self, cypher: LiteralString, **params: Any) -> list[dict[str, Any]]:
        """Auto-commit query; Memgraph requires it for schema statements and SHOW STORAGE INFO (spike T0.4)."""
        async with self._driver.session() as session:
            return [r.data() async for r in await session.run(cypher, params)]

    async def init_schema(self, cypher_file: Path) -> None:
        """Run contracts/kg/init.cypher statement by statement (idempotent)."""
        code = "\n".join(
            line for line in cypher_file.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("//")
        )
        for statement in filter(None, (s.strip() for s in code.split(";"))):
            await self.run_autocommit(statement)


class RunRecorder:
    """Creates and finishes Run nodes (contracts/kg/schema.yaml)."""

    def __init__(self, kg: KgClient, agent: str, agent_version: str) -> None:
        self._kg, self._agent, self._agent_version = kg, agent, agent_version

    async def start(
        self,
        *,
        project_id: str,
        skill: str,
        tool: str,
        tool_version: str,
        input_key: str,
        model: str | None = None,
        params: str | None = None,
        config_hash: str | None = None,
        used_version_ids: Sequence[str] = (),
        workflow_id: str | None = None,
        task_id: str | None = None,
        triggered_by: str | None = None,
        replay_mode: str | None = None,
        prompt_ids: Sequence[str] = (),
    ) -> str:
        """Create a running Run and USED edges; return its id. replay_mode defaults to SANDMAN_LLM_MODE."""
        run_id = new_run_id()
        props = {
            "id": run_id, "projectId": project_id, "agent": self._agent, "agentVersion": self._agent_version,
            "skill": skill, "tool": tool, "toolVersion": tool_version, "inputKey": input_key, "model": model,
            "params": params, "configHash": config_hash, "workflowId": workflow_id, "a2aTaskId": task_id,
            "triggeredBy": triggered_by, "replayMode": replay_mode or llm_mode(),
            "status": "running", "startedAt": now(), "promptIds": list(prompt_ids) or None,
        }  # fmt: skip
        await self._kg.write(
            "CREATE (r:Run) SET r = $props "
            "WITH r UNWIND $versions AS vid MATCH (v:ArtifactVersion {id: vid}) CREATE (r)-[:USED]->(v)",
            props={k: v for k, v in props.items() if v is not None},
            versions=list(used_version_ids),
        )
        return run_id

    async def complete(
        self,
        run_id: str,
        usage: Usage | None = None,
        status: str = "completed",
        resolved_model: str | None = None,
        mixed_models: bool = False,
    ) -> None:
        """Mark a run completed (or partial) with token usage, the first resolved model and the mixed-models flag."""
        await self._kg.write(
            "MATCH (r:Run {id: $id}) SET r.status = $status, r.endedAt = $now, "
            "r.inputTokens = $inp, r.outputTokens = $out, r.resolvedModel = $resolved, r.mixedModels = $mixed",
            id=run_id, status=status, now=now(), resolved=resolved_model, mixed=True if mixed_models else None,
            inp=usage.input_tokens if usage else None, out=usage.output_tokens if usage else None,
        )  # fmt: skip

    async def fail(self, run_id: str, error: str) -> None:
        """Mark a run failed."""
        await self._kg.write(
            "MATCH (r:Run {id: $id}) SET r.status = 'failed', r.error = $error, r.endedAt = $now",
            id=run_id, error=error, now=now(),
        )  # fmt: skip

    async def cleanup_stale(self, input_key: str) -> int:
        """Delete facts created by non-completed runs with this input key; mark those runs failed."""
        rows = await self._kg.write(
            "MATCH (r:Run {inputKey: $key}) WHERE r.status IN ['running', 'failed'] "
            "OPTIONAL MATCH (r)-[:PRODUCED|DETECTED]->(f) WHERE f.runId = r.id "
            "WITH r, collect(f) AS facts "
            "SET r.status = 'failed', r.endedAt = coalesce(r.endedAt, $now) "
            "WITH facts UNWIND facts AS f DETACH DELETE f RETURN count(f) AS deleted",
            key=input_key, now=now(),
        )  # fmt: skip
        return int(rows[0]["deleted"]) if rows else 0


async def _migrate() -> None:
    from sandman_agent.config import Settings

    settings = Settings.from_env()
    settings.require("kg_uri")
    kg = KgClient(settings.kg_uri or "", settings.kg_user, settings.kg_password)
    try:
        await kg.init_schema(settings.contracts_dir / "kg" / "init.cypher")
    finally:
        await kg.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_migrate())
