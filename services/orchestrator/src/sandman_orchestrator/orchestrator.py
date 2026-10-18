"""Workflow state in SQLite, event intake (inbox) and the step executor."""

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from sandman_agent import A2aClient, ContractError, Contracts, Event, TaskNotFoundError, TaskSnapshot, telemetry
from sandman_agent.ids import canonical_json, new_workflow_id, sha256_hex

from sandman_orchestrator.rules import SKILL_AGENT, Graph, Rules, StepSpec, manual, merge, plan

log = logging.getLogger(__name__)

ACTIVE = ("PENDING", "RUNNING")
ACTIVITY_MAX = 200
MAX_DELIVER = 5  # EventConsumer.config().max_deliver


def action_key(agent: str, skill: str, input: Mapping[str, Any], trigger_id: str) -> str:
    return sha256_hex(canonical_json({"agent": agent, "skill": skill, "input": dict(input), "triggerId": trigger_id}))


def workflow_status(statuses: Sequence[str]) -> str:
    """Derived from the steps; RUNNING while any step is pending or running."""
    s = set(statuses)
    if s and s <= {"PENDING"}:
        return "PENDING"
    if s & {"PENDING", "RUNNING"}:
        return "RUNNING"
    return next((x for x in ("BLOCKED", "FAILED", "CANCELED") if x in s), "SUCCEEDED")


def error(code: str, message: str, retryable: bool) -> dict[str, Any]:
    return {"code": code, "message": message, "retryable": retryable}


class Conflict(Exception):
    """Nothing to cancel, or not BLOCKED."""


class Orchestrator:
    def __init__(
        self,
        db_path: str | Path,
        rules: Rules,
        graph: Graph,
        a2a: A2aClient,
        agents: Mapping[str, str],
        contracts: Contracts,
        clock: Callable[[], float] = time.time,
        tick: float = 1.0,
    ) -> None:
        self.rules, self.graph, self.a2a, self.agents, self.contracts = rules, graph, a2a, agents, contracts
        self.clock, self.tick_interval = clock, tick
        self.db = sqlite3.connect(db_path, autocommit=False)
        self.db.row_factory = sqlite3.Row
        self._migrate()
        self.wake = asyncio.Event()
        self._next_schedule = 0.0

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for n, script in enumerate(sorted((Path(__file__).parent / "migrations").glob("*.sql")), 1):
            if n > version:
                try:
                    self.db.executescript(f"{script.read_text()}; PRAGMA user_version = {n};")
                except Exception:
                    self.db.rollback()
                    raise
                self.db.commit()

    def close(self) -> None:
        self.db.close()

    def iso(self, t: float | None = None) -> str:
        return datetime.fromtimestamp(self.clock() if t is None else t, UTC).isoformat(timespec="seconds")[:-6] + "Z"

    # ------------------------------------------------------------------ intake

    async def consume(self, event: Event, delivered: int) -> None:
        """Consumer handler: contract-invalid events and the last failed delivery go to dead_events (then acked)."""
        try:
            self.contracts.validate("events.schema.json", event.to_json())
            await self.handle(event)
        except Exception as e:
            if not isinstance(e, ContractError) and delivered < MAX_DELIVER:
                raise
            log.exception("dead event", extra={"fields": {"event": event.type, "eventId": event.id}})
            with self.db:
                self.db.execute(
                    "INSERT OR REPLACE INTO dead_events VALUES (?, ?, ?, ?, ?)",
                    (event.id, event.type, json.dumps(event.to_json()), f"{type(e).__name__}: {e}", self.iso()),
                )

    async def handle(self, event: Event) -> None:
        """Process one event exactly once: inbox row and resulting steps in one transaction."""
        row = self.db.execute("SELECT processed_at FROM inbox WHERE event_id = ?", (event.id,)).fetchone()
        if row and row["processed_at"]:
            return
        if event.type == "sandman.inconsistency.status.changed":
            log.info("inconsistency status changed", extra={"fields": {"audit": dict(event.data)}})
        specs = await plan(event, self.graph, self.rules)
        with self.db:
            now = self.iso()
            self.db.execute(
                "INSERT INTO inbox VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (event_id) DO UPDATE SET processed_at = excluded.processed_at",
                (event.id, event.type, event.subject, json.dumps(event.to_json()), now, now),
            )
            wf_id = self._complete(event)
            if wf_id is False:
                return
            if wf_id is None:
                trigger = {"kind": "event", "eventType": event.type, "eventId": event.id}
                self._add_steps(event.subject, trigger, specs, event.id, force=False)
            else:
                self._add_steps(event.subject, wf_id, specs, event.id, force=False)
        self.wake.set()

    def _complete(self, event: Event) -> str | Literal[False] | None:
        """Complete the step whose attempt sent messageId = causationid. Returns its workflow id, None if no
        step matched, False if follow-ups are suppressed (late event or single-step manual workflow)."""
        if not event.causation_id:
            return None
        step = self.db.execute(
            "SELECT s.*, m.rowid = (SELECT max(rowid) FROM messages WHERE step_id = s.id) AS current "
            "FROM messages m JOIN steps s ON s.id = m.step_id WHERE m.message_id = ?",
            (event.causation_id,),
        ).fetchone()
        if step is None:
            return None
        if step["status"] not in ACTIVE or not step["current"]:
            log.info("stale completion event ignored", extra={"fields": {"step": step["id"]}})
            return False
        self.db.execute(
            "UPDATE steps SET status = 'SUCCEEDED', result_json = ?, ended_at = ?, next_poll_at = NULL WHERE id = ?",
            (json.dumps(dict(event.data)), self.iso(), step["id"]),
        )
        self._touch(step["workflow_id"])
        trigger = json.loads(self._workflow(step["workflow_id"])["trigger_json"])
        if trigger["kind"] == "manual" and trigger.get("type") not in ("full", "prune"):
            return False
        return str(step["workflow_id"])

    def _workflow(self, wf_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.db.execute("SELECT * FROM workflows WHERE id = ?", (wf_id,)).fetchone()
        return row

    def _add_steps(
        self, project: str, workflow: str | dict[str, Any], specs: Sequence[StepSpec], trigger_id: str, force: bool
    ) -> str | None:
        """Insert steps (deduplicated by actionKey unless force, coalesced if mergeable). `workflow` is an existing
        id or a trigger for a new workflow, created only when a step is inserted (always for manual triggers)."""
        now = self.clock()
        wf_id = workflow if isinstance(workflow, str) else None
        if isinstance(workflow, dict) and workflow["kind"] == "manual":
            wf_id = self._new_workflow(project, workflow)
        for spec in specs:
            agent = SKILL_AGENT[spec.skill]
            key = action_key(agent, spec.skill, spec.input, trigger_id)
            if (
                not force
                and self.db.execute(
                    "SELECT 1 FROM steps WHERE action_key = ? AND status NOT IN ('FAILED', 'CANCELED')", (key,)
                ).fetchone()
            ):
                continue
            if spec.mergeable:
                pending = self.db.execute(
                    "SELECT * FROM steps WHERE project_id = ? AND skill = ? AND status = 'PENDING' AND attempts = 0 "
                    "AND mergeable = 1 ORDER BY rowid",
                    (project, spec.skill),
                ).fetchall()
                target = next((p for p in pending if merge(json.loads(p["input_json"]), spec.input) is not None), None)
                if target is not None:
                    merged = merge(json.loads(target["input_json"]), spec.input)
                    assert merged is not None
                    if wf_id is None:
                        assert isinstance(workflow, dict)
                        wf_id = self._new_workflow(project, workflow)
                    if target["workflow_id"] != wf_id:
                        self._move_merged(target, wf_id)
                    self.db.execute(
                        "UPDATE steps SET input_json = ?, action_key = ?, next_attempt_at = ? WHERE id = ?",
                        (json.dumps(merged), action_key(agent, spec.skill, merged, trigger_id),
                         now + self.rules.debounce, target["id"]),
                    )  # fmt: skip
                    self._touch(wf_id)
                    continue
            if wf_id is None:
                assert isinstance(workflow, dict)
                wf_id = self._new_workflow(project, workflow)
            self.db.execute(
                "INSERT INTO steps (id, workflow_id, project_id, agent, skill, input_json, action_key, mergeable, "
                "status, next_attempt_at, traceparent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)",
                (f"step_{uuid.uuid4().hex}", wf_id, project, agent, spec.skill, json.dumps(spec.input), key,
                 spec.mergeable, now + (self.rules.debounce if spec.mergeable else 0),
                 telemetry.trace_headers().get("traceparent")),
            )  # fmt: skip
            self._touch(wf_id)
        return wf_id

    def _move_merged(self, target: sqlite3.Row, new_wf: str) -> None:
        """A merged step belongs to the newest workflow; the old one keeps a SUCCEEDED marker (R-023)."""
        self.db.execute(
            "INSERT INTO steps (id, workflow_id, project_id, agent, skill, input_json, action_key, mergeable, status, "
            "next_attempt_at, ended_at, merged_into) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'SUCCEEDED', ?, ?, ?)",
            (f"step_{uuid.uuid4().hex}", target["workflow_id"], target["project_id"], target["agent"],
             target["skill"], target["input_json"], target["action_key"], self.clock(), self.iso(), target["id"]),
        )  # fmt: skip
        self.db.execute("UPDATE steps SET workflow_id = ? WHERE id = ?", (new_wf, target["id"]))
        self._touch(target["workflow_id"])

    def _new_workflow(self, project: str, trigger: dict[str, Any]) -> str:
        wf_id, now = new_workflow_id(), self.iso()
        self.db.execute(
            "INSERT INTO workflows VALUES (?, ?, ?, 'SUCCEEDED', ?, ?)", (wf_id, project, json.dumps(trigger), now, now)
        )
        return wf_id

    def _touch(self, wf_id: str) -> None:
        statuses = [r[0] for r in self.db.execute("SELECT status FROM steps WHERE workflow_id = ?", (wf_id,))]
        self.db.execute(
            "UPDATE workflows SET status = ?, updated_at = ? WHERE id = ?",
            (workflow_status(statuses), self.iso(), wf_id),
        )

    async def trigger(
        self,
        kind: str,
        project: str,
        params: Mapping[str, Any],
        actor: str,
        force: bool = False,
        request_id: str | None = None,
    ) -> str:
        """Manual workflow; ContractError for invalid skill input, ValueError if there is nothing to run."""
        specs = await manual(kind, project, params, self.graph, self.rules)
        if not specs:
            raise ValueError(f"nothing to run for {kind} in project {project} (no matching current artifact versions)")
        for spec in specs:
            schema = "".join(w.capitalize() for w in spec.skill.split("-")) + "Input"
            self.contracts.validate(f"skills.schema.json#/$defs/{schema}", spec.input)
        trigger = {"kind": "manual", "type": kind, "actor": actor}
        with self.db:
            wf_id = self._add_steps(project, trigger, specs, request_id or str(uuid.uuid4()), force)
        self.wake.set()
        assert wf_id is not None
        return wf_id

    def retry(self, wf_id: str) -> bool:
        """Resume a BLOCKED workflow: its BLOCKED steps become PENDING. False if unknown, Conflict if not BLOCKED."""
        with self.db:
            wf = self._workflow(wf_id)
            if wf is None:
                return False
            if wf["status"] != "BLOCKED":
                raise Conflict(wf_id)
            self.db.execute(
                "UPDATE steps SET status = 'PENDING', attempts = 0, error_json = NULL, ended_at = NULL, "
                "a2a_task_id = NULL, next_attempt_at = ? WHERE workflow_id = ? AND status = 'BLOCKED'",
                (self.clock(), wf_id),
            )
            self._touch(wf_id)
        self.wake.set()
        return True

    async def replay(self, event_id: str) -> bool:
        """Move a dead event back into the inbox and process it."""
        with self.db:
            dead = self.db.execute("SELECT * FROM dead_events WHERE event_id = ?", (event_id,)).fetchone()
            if dead is None:
                return False
            self.db.execute(
                "INSERT OR IGNORE INTO inbox SELECT event_id, type, json_extract(payload, '$.subject'), payload, ?, "
                "NULL FROM dead_events WHERE event_id = ?",
                (self.iso(), event_id),
            )
            self.db.execute("DELETE FROM dead_events WHERE event_id = ?", (event_id,))
        await self.process_inbox()
        return True

    async def process_inbox(self) -> None:
        """Process unprocessed inbox rows (replays); failures go back to dead_events."""
        for row in self.db.execute("SELECT * FROM inbox WHERE processed_at IS NULL").fetchall():
            try:
                await self.handle(Event.from_json(json.loads(row["payload"])))
            except Exception as e:  # noqa: BLE001 - kept for the next replay
                log.exception("replay failed")
                with self.db:
                    self.db.execute("DELETE FROM inbox WHERE event_id = ?", (row["event_id"],))
                    self.db.execute(
                        "INSERT OR REPLACE INTO dead_events VALUES (?, ?, ?, ?, ?)",
                        (row["event_id"], row["type"], row["payload"], f"{type(e).__name__}: {e}", self.iso()),
                    )

    async def cancel(self, wf_id: str) -> bool:
        """Cancel pending and running steps; False if the workflow is unknown, Conflict if nothing is active."""
        with self.db:
            if self.db.execute("SELECT 1 FROM workflows WHERE id = ?", (wf_id,)).fetchone() is None:
                return False
            steps = self.db.execute(
                "SELECT * FROM steps WHERE workflow_id = ? AND status IN ('PENDING', 'RUNNING')", (wf_id,)
            ).fetchall()
            if not steps:
                raise Conflict(wf_id)
            self.db.executemany(
                "UPDATE steps SET status = 'CANCELED', ended_at = ?, next_poll_at = NULL WHERE id = ?",
                [(self.iso(), s["id"]) for s in steps],
            )
            self._touch(wf_id)
        for s in steps:
            if s["status"] == "RUNNING" and s["a2a_task_id"]:
                await self._cancel_task(s)
        return True

    # ---------------------------------------------------------------- executor

    async def recover(self) -> None:
        """Startup: poll RUNNING steps now, process unprocessed inbox rows."""
        with self.db:
            self.db.execute("UPDATE steps SET next_poll_at = ? WHERE status = 'RUNNING'", (self.clock(),))
        await self.process_inbox()

    async def run(self) -> None:
        """Executor loop; woken by new steps."""
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("executor tick failed")
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), self.tick_interval)
            except TimeoutError:
                pass

    async def schedule_retention(self) -> None:
        """Start a prune workflow per enabled project when the last one is older than the interval."""
        retention = self.rules.retention
        if not retention.enabled or self.clock() < self._next_schedule:
            return
        self._next_schedule = self.clock() + 60
        for project in await self.graph.projects():
            enabled, _, _ = retention.project(project)
            last = self.db.execute(
                "SELECT max(created_at) FROM workflows WHERE project_id = ? "
                "AND json_extract(trigger_json, '$.type') = 'prune'",
                (project,),
            ).fetchone()[0]
            if not enabled or (last and datetime.fromisoformat(last).timestamp() + retention.interval > self.clock()):
                continue
            try:
                await self.trigger("prune", project, {}, "system:retention-scheduler")
            except Exception:
                log.exception("scheduled prune failed", extra={"fields": {"project": project}})

    async def tick(self) -> None:
        """Start due steps (pipeline lock per project, per-agent limit), then poll due running steps.
        ponytail: submits and polls run sequentially; a hanging agent delays others by the HTTP timeout (30 s)."""
        await self.schedule_retention()
        now = self.clock()
        running = self.db.execute("SELECT project_id, agent FROM steps WHERE status = 'RUNNING'").fetchall()
        locked = {r["project_id"] for r in running}
        per_agent = Counter(r["agent"] for r in running)
        # a step waiting for its retry keeps the project lock
        retrying = {
            r["project_id"]: r["id"]
            for r in self.db.execute("SELECT id, project_id FROM steps WHERE status = 'PENDING' AND attempts > 0")
        }
        due = self.db.execute(
            "SELECT * FROM steps WHERE status = 'PENDING' AND next_attempt_at <= ? ORDER BY rowid", (now,)
        ).fetchall()
        for step in due:
            p = step["project_id"]
            if (
                p in locked
                or retrying.get(p, step["id"]) != step["id"]
                or per_agent[step["agent"]] >= self.rules.max_per_agent
            ):
                continue
            locked.add(p)
            per_agent[step["agent"]] += 1
            await self._start(step["id"])
        polls = self.db.execute(
            "SELECT * FROM steps WHERE status = 'RUNNING' AND next_poll_at <= ?", (self.clock(),)
        ).fetchall()
        for step in polls:
            await self._poll(step)

    async def _start(self, step_id: str) -> None:
        now = self.clock()
        with self.db:  # store the messageId before SendMessage so a fast completion event always matches
            step = self.db.execute("SELECT * FROM steps WHERE id = ? AND status = 'PENDING'", (step_id,)).fetchone()
            if step is None:
                return
            last = (
                self.db.execute(
                    "SELECT message_id FROM messages WHERE step_id = ? ORDER BY rowid DESC LIMIT 1", (step_id,)
                ).fetchone()
                if step["attempts"] and step["a2a_task_id"] is None
                else None
            )
            message_id = last["message_id"] if last else str(uuid.uuid4())
            if not last:
                self.db.execute("INSERT INTO messages VALUES (?, ?)", (message_id, step_id))
            self.db.execute(
                "UPDATE steps SET status = 'RUNNING', attempts = attempts + 1, a2a_task_id = NULL, attempt_at = ?, "
                "started_at = coalesce(started_at, ?), next_poll_at = ?, activity_text = NULL, activity_at = NULL "
                "WHERE id = ?",
                (now, self.iso(now), now + self.rules.poll_initial, step_id),
            )
            self._touch(step["workflow_id"])
        trigger = json.loads(self._workflow(step["workflow_id"])["trigger_json"])
        attempt = step["attempts"] + 1
        token = telemetry.bind(workflowId=step["workflow_id"], projectId=step["project_id"])
        try:
            log.info("submitting step", extra={"fields": {"step": step_id, "skill": step["skill"], "attempt": attempt}})
            with telemetry.continued_span(f"workflow.step {step['skill']}", step["traceparent"]):
                snap = await self.a2a.submit(
                    self.agents[step["agent"]],
                    step["skill"],
                    json.loads(step["input_json"]),
                    workflow_id=step["workflow_id"],
                    actor=trigger.get("actor") if trigger["kind"] == "manual" else None,
                    message_id=message_id,
                )
        except Exception as e:  # noqa: BLE001 - connection errors and agent errors are retried
            log.warning("submit failed", extra={"fields": {"step": step_id, "error": str(e)}})
            if not isinstance(e, httpx.TransportError):
                with self.db:
                    self.db.execute(
                        "UPDATE steps SET a2a_task_id = '' WHERE id = ? AND attempts = ?", (step_id, attempt)
                    )
            self._failed(step_id, attempt, error("DEPENDENCY_UNAVAILABLE", f"{type(e).__name__}: {e}", True))
            return
        finally:
            telemetry.reset(token)
        with self.db:
            self.db.execute(
                "UPDATE steps SET a2a_task_id = ? WHERE id = ? AND attempts = ?", (snap.task_id, step_id, attempt)
            )
        await self._on_snapshot(step_id, attempt, snap)

    async def _poll(self, step: sqlite3.Row) -> None:
        if not step["a2a_task_id"]:  # crashed between storing the attempt and SendMessage
            self._failed(step["id"], step["attempts"], error("TASK_NOT_FOUND", "attempt was not submitted", True))
            return
        try:
            snap = await self.a2a.get(self.agents[step["agent"]], step["a2a_task_id"])
        except TaskNotFoundError:
            self._failed(step["id"], step["attempts"], error("TASK_NOT_FOUND", "agent lost the task", True))
            return
        except Exception as e:  # noqa: BLE001 - agent unreachable: keep polling until the timeout
            log.warning("poll failed", extra={"fields": {"step": step["id"], "error": str(e)}})
            snap = TaskSnapshot(step["a2a_task_id"], "UNKNOWN")
        await self._on_snapshot(step["id"], step["attempts"], snap)

    async def _on_snapshot(self, step_id: str, attempt: int, snap: TaskSnapshot) -> None:
        """Failures end or retry the attempt; COMPLETED waits for the completion event."""
        step = self.db.execute(
            "SELECT * FROM steps WHERE id = ? AND status = 'RUNNING' AND attempts = ?", (step_id, attempt)
        ).fetchone()
        if step is None:
            return
        err = snap.error or error("INTERNAL", snap.state, False)
        now = self.clock()
        if snap.state == "TASK_STATE_FAILED" and err.get("code") == "BUDGET_EXCEEDED":
            self._finish(step, "BLOCKED", err)
        elif snap.state == "TASK_STATE_FAILED":
            self._failed(step_id, attempt, err)
        elif snap.state == "TASK_STATE_REJECTED":
            self._failed(step_id, attempt, {**err, "retryable": False})
        elif snap.state == "TASK_STATE_CANCELED":
            self._finish(step, "CANCELED", err)
        elif now >= step["attempt_at"] + self.rules.timeouts[step["skill"]]:
            if snap.state != "TASK_STATE_COMPLETED":  # a COMPLETED task whose event never came is retried
                await self._cancel_task(step)
            self._failed(step_id, attempt, error("TIMEOUT", f"{step['skill']} timed out", True))
        else:
            interval = min(self.rules.poll_max, max(self.rules.poll_initial, now - step["attempt_at"]))
            with self.db:
                text = (snap.activity or "")[:ACTIVITY_MAX]
                if text and text != step["activity_text"]:
                    self.db.execute(
                        "UPDATE steps SET activity_text = ?, activity_at = ? WHERE id = ?", (text, self.iso(), step_id)
                    )
                self.db.execute("UPDATE steps SET next_poll_at = ? WHERE id = ?", (now + interval, step_id))

    async def _cancel_task(self, step: sqlite3.Row) -> None:
        try:
            await self.a2a.cancel(self.agents[step["agent"]], step["a2a_task_id"])
        except Exception as e:  # noqa: BLE001 - best effort
            log.warning("CancelTask failed", extra={"fields": {"step": step["id"], "error": str(e)}})

    def _failed(self, step_id: str, attempt: int, err: Mapping[str, Any]) -> None:
        """Retry retryable failures up to maxAttempts with backoff, else FAILED."""
        step = self.db.execute(
            "SELECT * FROM steps WHERE id = ? AND status = 'RUNNING' AND attempts = ?", (step_id, attempt)
        ).fetchone()
        if step is None:
            return
        if not (err.get("retryable") and attempt < self.rules.max_attempts):
            self._finish(step, "FAILED", err)
            return
        delay = self.rules.backoff[min(attempt, len(self.rules.backoff)) - 1]
        log.info("retrying step", extra={"fields": {"step": step_id, "attempt": attempt, "error": dict(err)}})
        with self.db:
            self.db.execute(
                "UPDATE steps SET status = 'PENDING', error_json = ?, next_attempt_at = ?, next_poll_at = NULL "
                "WHERE id = ?",
                (json.dumps(dict(err)), self.clock() + delay, step_id),
            )
            self._touch(step["workflow_id"])

    def _finish(self, step: sqlite3.Row, status: str, err: Mapping[str, Any]) -> None:
        log.warning("step ended", extra={"fields": {"step": step["id"], "status": status, "error": dict(err)}})
        with self.db:
            self.db.execute(
                "UPDATE steps SET status = ?, error_json = ?, ended_at = ?, next_poll_at = NULL WHERE id = ?",
                (status, json.dumps(dict(err)), self.iso(), step["id"]),
            )
            self._touch(step["workflow_id"])

    # ------------------------------------------------------------------- reads

    def workflow_json(self, row: sqlite3.Row, with_steps: bool) -> dict[str, Any]:
        wf: dict[str, Any] = {
            "id": row["id"], "projectId": row["project_id"], "trigger": json.loads(row["trigger_json"]),
            "status": row["status"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }  # fmt: skip
        if with_steps:
            steps = self.db.execute("SELECT * FROM steps WHERE workflow_id = ? ORDER BY rowid", (row["id"],))
            wf["steps"] = [self._step_json(s) for s in steps]
        return wf

    @staticmethod
    def _step_json(s: sqlite3.Row) -> dict[str, Any]:
        step = {
            "id": s["id"], "agent": s["agent"], "skill": s["skill"], "status": s["status"], "attempts": s["attempts"],
            "actionKey": s["action_key"], "mergedInto": s["merged_into"], "a2aTaskId": s["a2a_task_id"] or None,
            "input": json.loads(s["input_json"]),
            "result": json.loads(s["result_json"] or "null"), "error": json.loads(s["error_json"] or "null"),
            "startedAt": s["started_at"], "endedAt": s["ended_at"],
        }  # fmt: skip
        if s["status"] == "RUNNING" and s["activity_text"]:
            step["activity"] = {"text": s["activity_text"], "at": s["activity_at"]}
        return {k: v for k, v in step.items() if v is not None}

    def get_workflow(self, wf_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM workflows WHERE id = ?", (wf_id,)).fetchone()
        return self.workflow_json(row, True) if row else None

    def list_workflows(self, project: str | None, status: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM workflows WHERE (?1 IS NULL OR project_id = ?1) AND (?2 IS NULL OR status = ?2) "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?3 OFFSET ?4",
            (project, status, limit, offset),
        ).fetchall()
        return [self.workflow_json(r, False) for r in rows]

    def dead_events(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM dead_events ORDER BY received_at").fetchall()
        return [
            {"eventId": r["event_id"], "type": r["type"], "error": r["error"], "receivedAt": r["received_at"]}
            for r in rows
        ]

    def counts(self) -> dict[str, int]:
        """Running steps, running workflows, pending steps."""

        def one(sql: str) -> int:
            return int(self.db.execute(sql).fetchone()[0])

        return {
            "runningSteps": one("SELECT count(*) FROM steps WHERE status = 'RUNNING'"),
            "runningWorkflows": one("SELECT count(*) FROM workflows WHERE status = 'RUNNING'"),
            "pendingSteps": one("SELECT count(*) FROM steps WHERE status = 'PENDING'"),
        }
