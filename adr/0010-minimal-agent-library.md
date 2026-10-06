---
status: accepted
date: 2026-10-06
---

# Minimal agent library per language

## Context and Problem Statement

Agents must not use agent frameworks (LangChain, LangGraph, DeepAgents, …) and call LLMs directly through the gateway. Common building blocks (LLM client, tools, loop, A2A glue, events, KG, telemetry, config) would otherwise be re-implemented per agent. What do the shared libraries contain, and how is Python/Java parity ensured?

## Decision Drivers

* No agent framework, no imposed control flow.
* Testability offline (mock LLM, record/replay).
* Identical behavior in Python and Java.
* Small surface; use official SDKs underneath.

## Considered Options

* Library scope: (S1) thin wrappers over official SDKs (OpenAI, A2A, Neo4j, NATS) behind small interfaces; (S2) raw HTTP/protocol implementations without SDKs; (S3) no shared library, each agent uses SDKs directly.
* Record/replay: (R1) at `LlmClient` level with a cross-language cassette format; (R2) HTTP level (vcrpy for Python, WireMock for Java).
* Parity: (P1) shared JSON Schemas + language-neutral conformance vectors; (P2) code generation of both libraries from one spec; (P3) review discipline only.

## Decision Outcome

Chosen: **S1 + R1 + P1**.

Modules: `config`, `ids`, `llm` (+ `ModelSelector` enforcing project LLM policy), `replay`, `tools`, `loop`, `a2a`, `events`, `kg` (+ `RunRecorder`), `artifacts`, `telemetry`, `contracts`, `testing`. Python API async; Java API synchronous on virtual threads; same names and semantics. Left out: prompt templating, memory, vector stores, planners, agent graphs, LLM retries/fallbacks (gateway), DI (except Quarkus CDI for A2A).

Cassette: JSONL entries `{key, request, response, resolvedModel, recordedAt}`, `key = sha256(RFC 8785 canonical JSON of the request)`; modes `live|record|replay` (strict replay).

### Consequences

* Good, because agents stay plain code with replaceable parts; tests run without network.
* Good, because cassettes are portable across languages and independent of SDK HTTP internals (e.g., OpenAI Python SDK's `httpx2` default client).
* Bad, because two implementations to maintain (kept small; conformance suite catches drift).
* Bad, because R1 does not test the HTTP layer (covered by the gateway smoke test and integration tests).

### Confirmation

Conformance vectors (`stable_id`, cassette keys, envelope serialization, tool-dispatch errors, skill invocation) pass in both CI jobs; a lint rule/dependency check rejects agent framework dependencies in agent code.

## Pros and Cons of the Options

### S1 thin wrappers

* Good, because maintained SDKs handle protocol details.
* Bad, because SDK churn (A2A SDK releases weekly) → pin versions.

### S2 raw implementations

* Good, because full control, fewer dependencies.
* Bad, because re-implementing A2A/OpenAI protocol details in two languages.

### S3 no library

* Good, because nothing to maintain centrally.
* Bad, because duplicated glue per agent and no parity guarantees.

### R1 client-level replay

* Good, because language-neutral and simple.
* Bad, because skips HTTP layer.

### R2 HTTP-level replay

* Good, because tests full stack.
* Bad, because two tools, two cassette formats; streaming/SSE replay behavior unverified.

### P1 schemas + vectors

* Good, because cheap and explicit.
* Bad, because does not cover every behavior.

### P2 code generation

* Good, because structural parity by construction.
* Bad, because generator tooling for two languages; behavior still hand-written.

### P3 review only

* Good, because no effort.
* Bad, because drift goes unnoticed.

## More Information

* LangChain4j inside reused LiSSA/ArDoCo code is acceptable (Q-2, answered 2026-10-06).
* Related: ADR-0001, ADR-0002, ADR-0004, ADR-0008.

## Amendments

* 2026-10-06, review decision D-3 (R-017): record/replay moves from the libraries to one OpenAI-compatible **`llm-replay` proxy** (Python service) in front of the gateway, used by all callers including LangChain4j inside LiSSA/ArDoCo, for chat and embeddings. Modes `live` (pass-through), `record`, `replay` (strict: miss → error). Cassette key = sha256 of the JCS-canonical OpenAI request body without `user`/`stream`/`stream_options`; the body contains the gateway model name, so re-pointing a route invalidates recordings. Streaming requests are rejected in record/replay mode (ceiling; SSE re-encoding if a caller needs it). Prompt-building KG reads must use `ORDER BY`; determinism is measured on a canonical KG export without ids and timestamps. The library `replay` modules and the cross-language cassette vectors for library clients are removed.
* 2026-10-06, review decision D-7 (R-051): until a consumer exists (M6 planner) the `tools`/`loop` modules are cut in both languages; the Java library has no `LlmClient`/`ModelSelector`: Java LLM use happens inside LangChain4j, configured by a small `LlmAccess` helper (base URL, key and route by project policy). Plugin clients: Python only A2A (extraction plugins), Java only HTTP (detector plugins).
* 2026-10-06, review decision D-1: skills return their output and completion event; the library validates the output, publishes the event, then completes the task.
