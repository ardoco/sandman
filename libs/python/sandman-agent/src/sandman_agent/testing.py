"""Test helpers: scripted LLM, in-memory event bus, conformance runner."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sandman_agent import ids
from sandman_agent.contracts import Contracts
from sandman_agent.events import Event, build_event
from sandman_agent.llm import ChatRequest, ChatResponse


class ScriptedLlmClient:
    """Returns queued responses in order and records every request."""

    def __init__(self, responses: Sequence[ChatResponse] = (), embeddings: Sequence[list[float]] = ()) -> None:
        self.responses, self.embeddings = list(responses), list(embeddings)
        self.requests: list[ChatRequest] = []

    async def chat(self, req: ChatRequest) -> ChatResponse:
        """Next scripted response."""
        self.requests.append(req)
        if not self.responses:
            raise AssertionError("no scripted response left")
        return self.responses.pop(0)

    async def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        """One scripted vector per text."""
        return [self.embeddings.pop(0) for _ in texts]


class InMemoryEventBus:
    """EventSink that validates and keeps published events."""

    def __init__(self, contracts: Contracts, source: str = "test") -> None:
        self._contracts, self._source = contracts, source
        self.events: list[Event] = []

    async def publish(
        self,
        type: str,
        subject: str,
        data: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        event_id: str | None = None,
    ) -> Event:
        """Validate and store."""
        event = build_event(self._contracts, self._source, type, subject, data, correlation_id, causation_id, event_id)
        self.events.append(event)
        return event


def run_conformance(vectors_path: Path, contracts: Contracts) -> list[str]:
    """Check the id, key, fingerprint and envelope vectors; return failure descriptions."""
    v = json.loads(vectors_path.read_text(encoding="utf-8"))
    failures: list[str] = []

    def check(label: str, got: object, expected: object) -> None:
        if got != expected:
            failures.append(f"{label}: got {got!r}, expected {expected!r}")

    for c in v["stableId"]["cases"]:
        check(f"stableId {c['parts']}", ids.stable_id(c["prefix"], *c["parts"]), c["expected"])
    for c in v["sectionKey"]["cases"]:
        check(f"sectionKey {c['headings']}", ids.section_local_key(c["headings"], c["occurrences"]), c["expected"])
    for c in v["sentenceKey"]["cases"]:
        got = ids.sentence_local_key(c["sectionLocalKey"], c["text"], c["occurrence"])
        check(f"sentenceKey {c['text']!r}", got, c["expected"])
    for c in v["fingerprint"]["cases"]:
        i = c["input"]
        fp = ids.fingerprint(i["type"], i["detector"], i["involved"])
        check(f"fingerprint {i['type']}", fp, c["expected"])
        check(f"inc id {i['type']}", ids.stable_id("inc", c["projectId"], fp), c["expectedInconsistencyId"])
    for c in v["eventEnvelope"]["cases"]:
        check(f"eventEnvelope {c['event']['id']}", contracts.is_valid("events.schema.json", c["event"]), c["valid"])
    return failures
