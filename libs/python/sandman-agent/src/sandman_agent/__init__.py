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
from sandman_agent.classify import (
    ARTIFACT_KINDS,
    CLASSIFIER_VERSION,
    RULE_KINDS,
    Verdict,
    guess,
    legacy_guess,
    resolve,
)
from sandman_agent.classify_tree import (
    BlockError,
    BlockState,
    apply_block,
    build_tree,
    diff,
    fold,
    generate_rules,
    generate_with_pins,
    parse_block,
    pin_rules,
    render_block,
    reviewed_rules,
    weak_fingerprint,
    write_atomic,
)
from sandman_agent.config import ConfigError, Settings
from sandman_agent.contracts import ContractError, Contracts
from sandman_agent.events import Event, EventConsumer, EventPublisher, EventSink, PublishError, ensure_stream
from sandman_agent.ids import (
    fingerprint,
    new_run_id,
    new_workflow_id,
    normalize_concept_name,
    section_local_key,
    sentence_local_key,
    stable_id,
)
from sandman_agent.kg import KgClient, RunRecorder
from sandman_agent.llm import ChatMessage, ChatRequest, ChatResponse, LlmClient, ModelSelector, OpenAiLlmClient, Usage
from sandman_agent.plugins import A2aPluginClient, PluginError
from sandman_agent.status import StatusTracker, status_route

__version__ = "0.1.0"

__all__ = [
    "BlockError",
    "BlockState",
    "apply_block",
    "build_tree",
    "diff",
    "fold",
    "generate_rules",
    "generate_with_pins",
    "parse_block",
    "pin_rules",
    "render_block",
    "reviewed_rules",
    "weak_fingerprint",
    "write_atomic",
    "A2aClient",
    "A2aPluginClient",
    "ARTIFACT_KINDS",
    "ArtifactStore",
    "CLASSIFIER_VERSION",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ConfigError",
    "ContractError",
    "Contracts",
    "Event",
    "EventConsumer",
    "EventPublisher",
    "EventSink",
    "KgClient",
    "LlmClient",
    "ModelSelector",
    "OpenAiLlmClient",
    "PluginError",
    "PublishError",
    "RULE_KINDS",
    "RunRecorder",
    "Settings",
    "Skill",
    "SkillContext",
    "SkillError",
    "SkillResult",
    "StatusTracker",
    "TaskNotFoundError",
    "TaskSnapshot",
    "TokenVerifier",
    "Usage",
    "Verdict",
    "ensure_stream",
    "fingerprint",
    "guess",
    "legacy_guess",
    "new_run_id",
    "new_workflow_id",
    "require_service",
    "resolve",
    "normalize_concept_name",
    "section_local_key",
    "sentence_local_key",
    "serve",
    "stable_id",
    "status_route",
    "trusted_actor",
]
