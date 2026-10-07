---
status: accepted
date: 2026-10-06
---

# Use A2A for commands to agents

## Context and Problem Statement

Agents are independent services in Python and Java that must be able to communicate. The orchestrator issues long-running work (extraction, TLR, detection) to agents. Should this use the Agent2Agent (A2A) protocol, plain REST, or something else — and for which interactions?

## Decision Drivers

* Long-running tasks need a lifecycle (submitted, working, completed, failed, canceled) and cancellation.
* Cross-language standard with maintained SDKs (Python, Java).
* Discovery of agent capabilities (skills, schemas), also for a later LLM planner.
* Domain events need fan-out; A2A is point-to-point only (no pub/sub).
* Simplicity for user-facing APIs (curl, browser, OpenAPI tooling).

## Considered Options

* A: A2A for commands to agents; events via broker; REST for queries/triage/uploads.
* B: A2A for everything between components.
* C: Plain REST (custom task resources) everywhere.

## Decision Outcome

Chosen option: "A". A2A spec **1.0.x**, **JSON-RPC 2.0 binding**, Agent Card at `/.well-known/agent-card.json`, `SendMessage` with `returnImmediately=true`, then `GetTask` polling; `CancelTask` on timeout. Skill selection via `message.metadata["sandman.skill"]`, input as one `data` part, result as task artifact `result` (SANDMAN convention, part of the contracts). HTTP bearer tokens declared in the card. Java agents run on Quarkus because the `a2a-java` server integration is CDI-based with Quarkus as reference runtime.

### Consequences

* Good, because task lifecycle, cancellation and discovery come from a standard with official SDKs (`a2a-sdk` for Python, `a2a-java`).
* Good, because external A2A agents could be integrated later.
* Bad, because A2A 1.0 broke compatibility with 0.3 (method names, Part structure, card fields); SDKs release frequently → versions must be pinned and samples written for 0.2/0.3 ignored.
* Bad, because the skill-selection convention is ours, not the spec's.
* Bad, because Java agents depend on Quarkus.

### Confirmation

Contract tests per agent: card validates against the A2A 1.0 schema; send/get/cancel round trip; invalid input → `TASK_STATE_REJECTED`.

## Pros and Cons of the Options

### A: A2A for commands only

* Good, because each interaction uses the simplest fitting mechanism.
* Bad, because three communication styles to understand (A2A, events, REST).

### B: A2A everywhere

* Good, because one protocol.
* Bad, because A2A has no pub/sub; events would become point-to-point calls to known receivers.
* Bad, because synchronous queries and CRUD-like triage become tasks with needless lifecycle overhead; browsers and curl are awkward clients.

### C: Plain REST everywhere

* Good, because no protocol dependency, trivial in both languages.
* Bad, because task lifecycle, cancel, discovery and error semantics must be designed and documented by us — re-inventing A2A.

## More Information

* Research basis: A2A spec v1.0.1 (2026-05-28), Linux Foundation project, Apache-2.0; `a2a-sdk` 1.2.2; `a2a-java` 1.4.0.Final (Java 17+).
* To verify: custom executor in plain Quarkus app; unknown-task error of `GetTask` (V-4, V-5).
* Revision (Q-13): static catalog lookups (TLR approaches, detectors) are plain REST (`GET /v1/approaches`, `GET /v1/detectors`), not A2A skills — no task lifecycle needed. Plugins may use A2A or a synchronous plain-HTTP contract (ADR-0012).
* Related: ADR-0001, ADR-0003, ADR-0006, ADR-0012.

## Amendments

* 2026-10-06, review decision D-1: `GetTask` polling no longer signals step completion; it only detects failures, rejections and timeouts (poll 30 s → 2 min). Steps complete on their completion event (ADR-0003 amendment).
* 2026-10-06, review decision D-6: the Java runtime (`a2a-java` on Quarkus) is provisional and decided in T0.4 against two criteria: (1) a custom `AgentExecutor` implementing the SANDMAN invocation convention runs in a plain Quarkus app; (2) a tlr-agent skeleton with `a2a-java` plus the ArDoCo and LiSSA dependencies builds and starts without dependency conflicts. If either fails, the Java agents use a hand-written JSON-RPC endpoint (`SendMessage`, `GetTask`, `CancelTask`, static Agent Card) without Quarkus, covered by the same conformance vectors. Default: `a2a-java`.
* 2026-10-07, D-6 decided by spike T0.4 (`docs/notes/spike-T0.4.md`, `spikes/T0.4/java-a2a/RESULT.md`): both criteria pass, Java agents use **`a2a-java` 1.4.0.Final on Quarkus 3.33.4**. Required settings: `a2a.authorization.required=false` with our bearer filter; the card's JSON-RPC path routed to a2a-java's root route; every client sends `A2A-Extensions` with the skill-invocation extension (required by a2a-java for `required: true` extensions) and `A2A-Version: 1.0`; integral doubles in data parts are normalized to integers in both languages.
