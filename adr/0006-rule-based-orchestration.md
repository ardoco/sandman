---
status: accepted
date: 2026-10-06
---

# Rule-based orchestration with optional LLM planner

## Context and Problem Statement

The orchestrator is informed about changes and decides which agent/service call runs next. Should this decision be rule-based, LLM-driven, or hybrid? How is workflow state tracked?

## Decision Drivers

* Reproducibility of experiments (same input → same sequence of calls).
* Cost and latency (an LLM call per event is wasteful for predictable reactions).
* Testability.
* Flexibility for ad-hoc user requests ("check docs of component X against code").

## Considered Options

* A: Rule table only.
* B: LLM planner for every decision.
* C: Hybrid — rules for event-driven reactions; LLM planner only for natural-language user requests, producing a validated plan.

## Decision Outcome

Chosen option: "C". Rules (`config/orchestrator/rules.yaml`) map events to A2A skill calls, with debounce/coalescing per project, idempotent action keys, per-agent concurrency limits, retries with backoff and timeouts. Workflow and step state in SQLite (`inbox`, `workflows`, `steps`, `dead_events`). The planner (later milestone, feature flag) outputs a JSON plan validated against Agent Card skill schemas and an allowlist; plans need user approval (workflow `BLOCKED` until approved).

### Consequences

* Good, because the automatic path is deterministic, cheap and unit-testable.
* Good, because the planner is confined to explicit user requests and cannot invent actions outside the skill catalog.
* Bad, because new automatic behavior requires editing rules (not "emergent").
* Bad, because the orchestrator is a single instance (ceiling: fine on one server; leader election not planned).

### Confirmation

Unit tests per rule (event in → expected task out); replaying a recorded JetStream log yields the same workflow steps.

## Pros and Cons of the Options

### A: Rules only

* Good, because simplest and reproducible.
* Bad, because no natural-language requests.

### B: LLM planner for everything

* Good, because flexible.
* Bad, because non-deterministic, costly, hard to test; risky with prompt injection from artifact-derived event data.

### C: Hybrid

* Good, because determinism where it matters, flexibility where requested.
* Bad, because two mechanisms.

## More Information

* Related: ADR-0002, ADR-0003.

## Amendments

* 2026-10-06, review decisions D-1 and D-5 (R-011, R-016, R-050):
  * Steps complete on their completion event (`causationid` = `messageId` of the A2A request); A2A polling only detects failures.
  * **Per-project pipeline lock:** at most one running step per project; extract → tlr → detect run strictly in sequence, and triggers arriving meanwhile coalesce into the next step. Projects run in parallel; the global per-agent limit stays.
  * Rules: `extraction.completed` without a configured TLR kind pair for the changed artifacts triggers detection on those artifacts; deletion-only ingestions trigger detection scoped to the deleted artifacts; detection scope is a set of **artifact** ids (the orchestrator resolves version ids to artifacts via a KG read).
  * `actionKey` includes the id of the triggering event or manual request (R-039, needed for reverts): only duplicates of the same trigger are suppressed.
