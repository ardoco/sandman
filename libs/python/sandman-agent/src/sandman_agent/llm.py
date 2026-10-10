"""LLM access through the llm-replay proxy and gateway (OpenAI-compatible API only)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from openai import APIStatusError, AsyncOpenAI

from sandman_agent import telemetry
from sandman_agent.contracts import Contracts

LOCAL_ONLY = "local-only"
REASONING_EFFORT_PREFIXES = ("ollama/",)  # cloud models reject the parameter unless they are reasoning models


class PolicyError(Exception):
    """A route is not allowed under the project's LLM policy."""


class BudgetExceededError(Exception):
    """The gateway budget of the virtual key is exhausted (Bifrost: HTTP 402, spike T0.4 check 5)."""


def _gateway_error(e: APIStatusError) -> Exception:
    # 402 = budget exhausted (blocks the workflow); 429 = rate limited, already retried by the SDK
    return BudgetExceededError(str(e)) if e.status_code == 402 else e


@dataclass(frozen=True)
class ChatMessage:
    """One chat message."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class Usage:
    """Token usage of one call."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ChatRequest:
    """Chat request on a logical route (e.g. chat-default)."""

    model: str
    messages: Sequence[ChatMessage]
    response_schema: Mapping[str, Any] | None = None
    temperature: float = 0.0
    seed: int | None = None
    max_tokens: int | None = None
    user: str | None = None  # run id for attribution
    reasoning_effort: str | None = None  # "none" switches thinking off; sent to self-hosted (ollama/) models only


@dataclass(frozen=True)
class ChatResponse:
    """Chat response with the model the gateway resolved."""

    message: ChatMessage
    finish_reason: str
    usage: Usage
    resolved_model: str


class LlmClient(Protocol):
    """Minimal LLM client interface."""

    async def chat(self, req: ChatRequest) -> ChatResponse:
        """One chat completion."""
        ...

    async def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        """Embeddings for texts on a logical route."""
        ...


@dataclass
class OpenAiLlmClient:
    """LlmClient over the official openai SDK; routes map logical names to gateway models."""

    base_url: str
    api_key: str
    routes: Mapping[str, str]
    local_only: bool = False
    _client: AsyncOpenAI | None = field(default=None, repr=False)

    def _model(self, route: str) -> str:
        if self.local_only and not route.endswith("-local"):
            raise PolicyError(f"route {route!r} is not allowed for local-only projects")
        try:
            return self.routes[route]
        except KeyError:
            raise PolicyError(f"unknown route {route!r}") from None

    def _sdk(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key, max_retries=2)
        return self._client

    async def chat(self, req: ChatRequest) -> ChatResponse:
        """Send one chat completion (structured output if response_schema is set)."""
        kwargs: dict[str, Any] = {
            "model": self._model(req.model),
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            "temperature": req.temperature,
        }
        if req.response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": dict(req.response_schema), "strict": True},
            }
        for key, value in (
            ("seed", req.seed),
            ("max_tokens", req.max_tokens),
            ("user", req.user),
            (
                "reasoning_effort",
                req.reasoning_effort if kwargs["model"].startswith(REASONING_EFFORT_PREFIXES) else None,
            ),
        ):
            if value is not None:
                kwargs[key] = value
        with telemetry.llm_span("chat", req.model, kwargs["model"]) as span:
            kwargs["extra_headers"] = telemetry.trace_headers()
            try:
                resp = await self._sdk().chat.completions.create(**kwargs)
            except APIStatusError as e:
                raise _gateway_error(e) from e
            choice = resp.choices[0]
            usage = Usage(resp.usage.prompt_tokens, resp.usage.completion_tokens) if resp.usage else Usage()
            span.update(response_model=resp.model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        return ChatResponse(
            ChatMessage("assistant", choice.message.content or ""), choice.finish_reason, usage, resp.model
        )

    async def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        """Embeddings for texts."""
        gateway_model = self._model(model)
        with telemetry.llm_span("embeddings", model, gateway_model) as span:
            try:
                resp = await self._sdk().embeddings.create(
                    model=gateway_model, input=list(texts), extra_headers=telemetry.trace_headers()
                )
            except APIStatusError as e:
                raise _gateway_error(e) from e
            span.update(response_model=resp.model)
            if resp.usage:
                span.update(input_tokens=resp.usage.prompt_tokens)
        return [d.embedding for d in resp.data]


class ModelSelector:
    """Picks routes and the virtual key by project LLM policy (models.yaml)."""

    def __init__(
        self, models_file: Path, base_url: str, key: str, local_key: str | None, contracts: Contracts | None = None
    ) -> None:
        if contracts is not None:
            data = contracts.load_config("llm-models", models_file)
        else:
            data = yaml.safe_load(models_file.read_text(encoding="utf-8")) or {}
        self.routes: dict[str, str] = dict(data.get("routes", {}))
        self._base_url, self._key, self._local_key = base_url, key, local_key

    def chat_model(self, policy: str) -> str:
        """Logical chat route for the policy."""
        return "chat-local" if policy == LOCAL_ONLY else "chat-default"

    def embed_model(self, policy: str) -> str:
        """Logical embedding route for the policy."""
        return "embed-local" if policy == LOCAL_ONLY else "embed-default"

    def client(self, policy: str) -> OpenAiLlmClient:
        """Client with the cloud or local-only virtual key."""
        if policy == LOCAL_ONLY:
            if not self._local_key:
                raise PolicyError("local-only project but no local virtual key configured")
            return OpenAiLlmClient(self._base_url, self._local_key, self.routes, local_only=True)
        return OpenAiLlmClient(self._base_url, self._key, self.routes)
