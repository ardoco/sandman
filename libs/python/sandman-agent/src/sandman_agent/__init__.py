"""Shared minimal agent library (ADR-0010): no control flow, thin wrappers over official SDKs."""

from sandman_agent.a2a import (
    A2aClient,
    Skill,
    SkillContext,
    SkillError,
    SkillResult,
    TaskNotFoundError,
    TaskSnapshot,
    serve,
)
from sandman_agent.artifacts import ArtifactStore
from sandman_agent.auth import TokenVerifier, require_service, trusted_actor
from sandman_agent.config import ConfigError, Settings
from sandman_agent.contracts import ContractError, Contracts
from sandman_agent.events import Event, EventConsumer, EventPublisher, EventSink, PublishError, ensure_stream
from sandman_agent.ids import fingerprint, new_run_id, new_workflow_id, section_local_key, sentence_local_key, stable_id
from sandman_agent.kg import KgClient, RunRecorder
from sandman_agent.llm import ChatMessage, ChatRequest, ChatResponse, LlmClient, ModelSelector, OpenAiLlmClient, Usage
from sandman_agent.plugins import A2aPluginClient, PluginError

__version__ = "0.1.0"

__all__ = [
    "A2aClient", "A2aPluginClient", "ArtifactStore", "ChatMessage", "ChatRequest", "ChatResponse", "ConfigError",
    "ContractError", "Contracts", "Event", "EventConsumer", "EventPublisher", "EventSink", "KgClient", "LlmClient",
    "ModelSelector", "OpenAiLlmClient", "PluginError", "PublishError", "RunRecorder", "Settings", "Skill",
    "SkillContext", "SkillError", "SkillResult", "TaskNotFoundError", "TaskSnapshot", "TokenVerifier", "Usage",
    "ensure_stream", "fingerprint", "new_run_id", "new_workflow_id", "require_service", "section_local_key",
    "sentence_local_key", "serve", "stable_id", "trusted_actor",
]  # fmt: skip
