---
status: accepted
date: 2026-10-06
---

# User interface for inconsistencies

## Context and Problem Statement

Users must be informed about inconsistencies, view them with evidence, and resolve or mark them. What is the minimal UI for the prototype?

## Decision Drivers

* Writes must go through the owner (inconsistency-agent) to apply lifecycle rules and publish events.
* Evidence (excerpts, line ranges, links, history) must be readable.
* No additional language/build chain if avoidable.
* Usable by non-developers in user studies.

## Considered Options

* A: Memgraph Lab.
* B: CLI only.
* C: REST API (owner) + server-rendered console in the query-service (FastAPI, Jinja2, vendored htmx; no own JavaScript).
* D: Separate single-page app (TypeScript), possibly reusing ArDoCo `traceview-v2` later.

## Decision Outcome

Chosen option: "C", plus a thin Python CLI for scripted tasks and CI ingestion. The REST triage API of the inconsistency-agent is the contract; the console calls it server-side with the authenticated actor (ADR-0013). Users are informed via per-user new/reopened badges and via structured `notification` log events (stdout, optional JSONL file) — the channels requested in Q-9. Memgraph Lab is not used (proprietary, Q-10).

### Consequences

* Good, because small, no third language, one exposed port; role checks in one place.
* Good, because the console can be replaced later without changing the triage contract.
* Bad, because limited interactivity (no graph visualization).
* Bad, because console releases are coupled to the query-service.

### Confirmation

End-to-end test: list → detail → transition → event published → history visible.

## Pros and Cons of the Options

### A: Memgraph Lab

* Good, because zero effort, good graph browsing.
* Bad, because writes bypass lifecycle rules and events; proprietary freeware (excluded by Q-10).

### B: CLI only

* Good, because cheapest.
* Bad, because poor for reading evidence; unsuitable for studies.

### C: Server-rendered console

* Good, because small and sufficient.
* Bad, because limited UI.

### D: SPA

* Good, because rich UI.
* Bad, because third language and build chain.

## More Information

* Related: ADR-0007, ADR-0009, ADR-0013.
