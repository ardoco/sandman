"""A2A glue: SANDMAN skill invocation convention on a2a-sdk (JSON-RPC binding).

Convention (Stage 2 §1.3): skill id in message.metadata['sandman.skill'], input = one data part, result =
task artifact 'result' with one data part, errors as SkillError in status message metadata 'sandman.error'.
Agent skills publish exactly one completion event (causationid = request messageId) after the output was
validated and before the task completes; plugin skills publish none.
"""

import asyncio
import hmac
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from a2a.client import Client, ClientConfig, create_client
from a2a.helpers.proto_helpers import new_data_part, new_task, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    SendMessageRequest,
    Task,
    TaskState,
)
from a2a.utils.errors import TaskNotFoundError
from google.protobuf.json_format import MessageToDict, ParseDict
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from sandman_agent import auth, telemetry
from sandman_agent.config import Settings
from sandman_agent.contracts import ContractError, Contracts
from sandman_agent.events import EventSink

log = logging.getLogger(__name__)

SKILL_KEY = "sandman.skill"
WORKFLOW_KEY = "sandman.workflowId"
ACTOR_KEY = "sandman.actor"
ERROR_KEY = "sandman.error"
INVOCATION_EXTENSION = "https://sandman.invalid/a2a/extensions/skill-invocation/v1"
__all__ = [
    "A2aClient", "Skill", "SkillContext", "SkillError", "SkillResult", "TaskNotFoundError", "TaskSnapshot", "serve",
]  # fmt: skip

TERMINAL_STATES = frozenset({"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"})


class SkillError(Exception):
    """Skill failure reported as SkillError (code, message, retryable)."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.retryable = code, message, retryable

    def to_json(self) -> dict[str, Any]:
        """contracts/common.schema.json#/$defs/SkillError."""
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


@dataclass(frozen=True)
class SkillResult:
    """Skill output plus the task's single completion event (None for plugin skills)."""

    output: Mapping[str, Any]
    event_type: str | None = None
    event_data: Mapping[str, Any] | None = None


@dataclass
class SkillContext:
    """Per-task context handed to a skill."""

    task_id: str
    message_id: str
    workflow_id: str | None
    caller: str
    actor: str | None
    settings: Settings
    updater: TaskUpdater = field(repr=False)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)

    async def progress(self, text: str) -> None:
        """Report progress as a TASK_STATE_WORKING status message."""
        await self.updater.start_work(self.updater.new_agent_message([new_text_part(text)]))


class Skill(Protocol):
    """A skill; input/output schemas come from the Agent Card's skill-invocation extension."""

    id: str

    async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
        """Do the work; raise SkillError for expected failures."""
        ...


def json_value(value: Any) -> Any:
    """Undo protobuf Struct number widening: integral floats back to int."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_value(v) for v in value]
    return value


def _struct(message: Any) -> dict[str, Any]:
    return json_value(MessageToDict(message)) if message is not None else {}


def load_card(path: Path) -> tuple[AgentCard, dict[str, dict[str, str]]]:
    """Agent Card from JSON plus the skill -> {inputSchema, outputSchema} map of the invocation extension."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    schemas: dict[str, dict[str, str]] = {}
    for ext in raw.get("capabilities", {}).get("extensions", []):
        if ext.get("uri") == INVOCATION_EXTENSION:
            schemas.update(ext.get("params", {}).get("skills", {}))
    return ParseDict(raw, AgentCard()), schemas


class _Executor(AgentExecutor):
    def __init__(
        self,
        skills: Mapping[str, Skill],
        schemas: Mapping[str, Mapping[str, str]],
        settings: Settings,
        contracts: Contracts,
        events: EventSink | None,
        authenticate: "_Authenticator",
    ) -> None:
        self._skills, self._schemas, self._settings = skills, schemas, settings
        self._contracts, self._events, self._authenticate = contracts, events, authenticate
        self._running: dict[str, SkillContext] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        assert message is not None and context.task_id and context.context_id
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task(context.task_id, context.context_id, TaskState.TASK_STATE_SUBMITTED)
            )
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        caller = self._authenticate(context.call_context.state.get("headers", {}).get("authorization")) or ""
        metadata = _struct(message.metadata)
        skill_id = metadata.get(SKILL_KEY)
        if not isinstance(skill_id, str):
            return await self._end(updater, SkillError("INVALID_INPUT", f"metadata {SKILL_KEY} missing", False), True)
        skill = self._skills.get(skill_id)
        if skill is None or skill_id not in self._schemas:
            return await self._end(updater, SkillError("UNKNOWN_SKILL", f"unknown skill {skill_id}", False), True)
        data_parts = [p for p in message.parts if p.HasField("data")]
        if len(data_parts) != 1:
            return await self._end(updater, SkillError("INVALID_INPUT", "expected exactly one data part", False), True)
        input = json_value(MessageToDict(data_parts[0].data))
        try:
            self._contracts.validate(self._schemas[skill_id]["inputSchema"], input)
        except ContractError as e:
            return await self._end(updater, SkillError("INVALID_INPUT", str(e), False), True)

        ctx = SkillContext(
            task_id=context.task_id,
            message_id=message.message_id,
            workflow_id=metadata.get(WORKFLOW_KEY) if isinstance(metadata.get(WORKFLOW_KEY), str) else None,
            caller=caller,
            actor=auth.actor_from_metadata(caller, metadata),
            settings=self._settings,
            updater=updater,
        )
        self._running[context.task_id] = ctx
        token = telemetry.bind(taskId=ctx.task_id, workflowId=ctx.workflow_id, actor=ctx.actor)
        try:
            await updater.start_work()
            await self._run(skill, ctx, input, updater)
        finally:
            self._running.pop(context.task_id, None)
            telemetry.reset(token)

    async def _run(self, skill: Skill, ctx: SkillContext, input: dict[str, Any], updater: TaskUpdater) -> None:
        try:
            result = await skill.run(ctx, input)
        except SkillError as e:
            return await self._end(updater, e, False)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - unexpected failures are reported as retryable INTERNAL
            log.exception("skill failed")
            return await self._end(updater, SkillError("INTERNAL", f"{type(e).__name__}: {e}", True), False)
        output = dict(result.output)
        try:
            self._contracts.validate(self._schemas[skill.id]["outputSchema"], output)
        except ContractError as e:
            return await self._end(updater, SkillError("INTERNAL", f"invalid output: {e}", False), False)
        if result.event_type is not None:
            if self._events is None or result.event_data is None:
                return await self._end(
                    updater, SkillError("INTERNAL", "completion event without event sink", False), False
                )
            try:
                await self._events.publish(
                    result.event_type,
                    str(input.get("projectId", "")),
                    result.event_data,
                    correlation_id=ctx.workflow_id,
                    causation_id=ctx.message_id,
                )
            except Exception as e:  # noqa: BLE001 - KG is committed; the retried task republishes
                log.exception("publishing completion event failed")
                return await self._end(updater, SkillError("DEPENDENCY_UNAVAILABLE", str(e), True), False)
        await updater.add_artifact([new_data_part(output)], name="result")
        await updater.complete()

    async def _end(self, updater: TaskUpdater, error: SkillError, reject: bool) -> None:
        msg = updater.new_agent_message([new_text_part(error.message)], metadata={ERROR_KEY: error.to_json()})
        await (updater.reject(msg) if reject else updater.failed(msg))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        ctx = self._running.get(context.task_id or "")
        if ctx is not None:
            ctx.cancelled.set()
        if context.task_id and context.context_id:
            await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


class _Authenticator(Protocol):
    def __call__(self, authorization: str | None) -> str | None: ...


def _authenticator(settings: Settings) -> _Authenticator:
    """Plugins check their inbound token; agents verify service tokens (plugin callers rejected)."""
    inbound = settings.plugin_inbound_token
    if inbound:

        def plugin_auth(authorization: str | None) -> str | None:
            token = (authorization or "")[7:].strip() if (authorization or "").lower().startswith("bearer ") else ""
            return "owner" if token and hmac.compare_digest(token, inbound) else None

        return plugin_auth
    verifier = auth.TokenVerifier(settings.service_verify)

    def service_auth(authorization: str | None) -> str | None:
        caller = verifier.caller_from_header(authorization)
        return None if caller is None or caller.startswith(auth.PLUGIN_PREFIX) else caller

    return service_auth


class _RequireToken:
    """Rejects JSON-RPC calls without an accepted bearer token (the card stays public)."""

    def __init__(self, app: ASGIApp, rpc_path: str, authenticate: _Authenticator) -> None:
        self.app, self.rpc_path, self.authenticate = app, rpc_path, authenticate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == self.rpc_path:
            if self.authenticate(Request(scope).headers.get("authorization")) is None:
                response: Response = JSONResponse({"error": "missing or invalid token"}, status_code=401)
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


def serve(
    agent_card_path: Path,
    skills: Sequence[Skill],
    settings: Settings,
    *,
    events: EventSink | None = None,
    contracts: Contracts | None = None,
) -> Starlette:
    """Starlette app serving the Agent Card and the JSON-RPC endpoint of the card's interface URL."""
    card, schemas = load_card(agent_card_path)
    missing = [s.id for s in skills if s.id not in schemas]
    if missing:
        raise ValueError(f"skills without schemas in {agent_card_path.name}: {missing}")
    authenticate = _authenticator(settings)
    executor = _Executor(
        {s.id: s for s in skills},
        schemas,
        settings,
        contracts or Contracts(settings.contracts_dir),
        events,
        authenticate,
    )
    handler = DefaultRequestHandlerV2(executor, InMemoryTaskStore(), card)
    rpc_path = urlsplit(card.supported_interfaces[0].url).path or "/"
    app = Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, rpc_path)])
    app.add_middleware(_RequireToken, rpc_path=rpc_path, authenticate=authenticate)
    return app


@dataclass(frozen=True)
class TaskSnapshot:
    """State of a task as seen by a caller."""

    task_id: str
    state: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @property
    def terminal(self) -> bool:
        """True for COMPLETED, FAILED, CANCELED, REJECTED."""
        return self.state in TERMINAL_STATES


def snapshot(task: Task) -> TaskSnapshot:
    """TaskSnapshot from an A2A Task."""
    result = None
    for artifact in task.artifacts:
        if artifact.name == "result":
            data = [p for p in artifact.parts if p.HasField("data")]
            result = json_value(MessageToDict(data[0].data)) if data else None
    error = _struct(task.status.message.metadata).get(ERROR_KEY) if task.status.HasField("message") else None
    return TaskSnapshot(task.id, TaskState.Name(task.status.state), result, error)


class A2aClient:
    """Submits skill tasks (returnImmediately), polls and cancels them, with this service's own token."""

    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30.0) -> None:
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, transport=transport, timeout=timeout
        )
        self._clients: dict[str, Client] = {}

    async def close(self) -> None:
        """Close connections."""
        for c in self._clients.values():
            await c.close()
        await self._http.aclose()

    async def card(self, agent_url: str) -> dict[str, Any]:
        """The agent's card as JSON."""
        resp = await self._http.get(agent_url.rstrip("/") + "/.well-known/agent-card.json")
        resp.raise_for_status()
        card: dict[str, Any] = resp.json()
        return card

    async def _client(self, agent_url: str) -> Client:
        if agent_url not in self._clients:
            card = ParseDict(await self.card(agent_url), AgentCard())
            base = urlsplit(agent_url)
            for interface in card.supported_interfaces:  # reach the agent at the configured address
                u = urlsplit(interface.url)
                interface.url = urlunsplit((base.scheme, base.netloc, u.path, u.query, u.fragment))
            config = ClientConfig(streaming=False, polling=True, httpx_client=self._http)
            self._clients[agent_url] = await create_client(card, client_config=config)
        return self._clients[agent_url]

    async def submit(
        self,
        agent_url: str,
        skill: str,
        input: Mapping[str, Any],
        *,
        workflow_id: str | None = None,
        actor: str | None = None,
        message_id: str | None = None,
    ) -> TaskSnapshot:
        """Send the skill message; message_id should be stored by the caller before sending."""
        metadata: dict[str, Any] = {SKILL_KEY: skill}
        if workflow_id:
            metadata[WORKFLOW_KEY] = workflow_id
        if actor:
            metadata[ACTOR_KEY] = actor
        message = {
            "messageId": message_id or str(uuid.uuid4()),
            "role": "ROLE_USER",
            "metadata": metadata,
            "parts": [{"data": dict(input)}],
        }
        return await self.send(agent_url, message)

    async def send(self, agent_url: str, message: Mapping[str, Any]) -> TaskSnapshot:
        """Send an A2A message given as proto JSON with returnImmediately."""
        request = ParseDict(
            {"message": dict(message), "configuration": {"returnImmediately": True}}, SendMessageRequest()
        )
        client = await self._client(agent_url)
        async for response in client.send_message(request):
            if response.HasField("task"):
                return snapshot(response.task)
        raise RuntimeError(f"{agent_url} returned no task")

    async def get(self, agent_url: str, task_id: str) -> TaskSnapshot:
        """Current state, result and error of a task."""
        return snapshot(await (await self._client(agent_url)).get_task(GetTaskRequest(id=task_id)))

    async def cancel(self, agent_url: str, task_id: str) -> TaskSnapshot:
        """Request cancellation."""
        return snapshot(await (await self._client(agent_url)).cancel_task(CancelTaskRequest(id=task_id)))
