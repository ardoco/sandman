---
status: accepted
date: 2026-10-06
---

# NATS JetStream as event bus

## Context and Problem Statement

The orchestrator must be informed about changes (new/changed artifacts, extraction finished, new trace links, new inconsistencies, status changes). Some changes are not caused by the orchestrator (uploads, user triage). How are these notifications delivered?

## Decision Drivers

* At-least-once delivery, also while the orchestrator restarts.
* Fan-out to more than one consumer later (notifier, experiment logger).
* Replayable event log for reproducibility of experiments.
* Small footprint on one server; clients for Python and Java; open-source license.

## Considered Options

* A: Agents push events to the orchestrator via HTTP/A2A.
* B: NATS JetStream.
* C: Redis Streams (or Valkey).
* D: RabbitMQ.
* E: Memgraph triggers publishing out of the database.
* F: Transactional outbox in the KG (`:Event` nodes polled by the orchestrator).

## Decision Outcome

Chosen option: "B", because it gives durable at-least-once delivery, producer deduplication (`Nats-Msg-Id`, 2 min window) and a replayable file-backed log in one small Apache-2.0 container with official Python (`nats-py`) and Java (`jnats`) clients.

Rules: CloudEvents 1.0 envelope with `correlationid`, `causationid`, `traceparent`; subjects `sandman.<eventType>`; one stream `SANDMAN` (`sandman.>`, file storage, 90-day retention); producers follow "commit KG, publish and await ack, then complete task"; the orchestrator uses a durable pull consumer with explicit ack, `MaxDeliver` 5 with backoff, and an inbox table keyed by event id.

### Consequences

* Good, because producers are decoupled from consumer availability.
* Good, because the stream doubles as an experiment log.
* Bad, because KG commit and publish are not atomic; a crash in between loses the event unless the A2A task is retried (handled: task completes only after publish ack; retries are idempotent). Intake via REST returns 202 only after publish ack.
* Bad, because one more container to operate and back up.

### Confirmation

Integration test: kill orchestrator, publish events, restart → all events processed exactly once (inbox dedupe); duplicate publish within window is rejected.

## Pros and Cons of the Options

### A: HTTP/A2A push

* Good, because no extra component.
* Bad, because each producer needs retry/outbox logic; orchestrator downtime blocks producers; no fan-out; A2A has no pub/sub.

### B: NATS JetStream

* Good, because of the drivers above.
* Bad, because less familiar than Redis/RabbitMQ to some developers.

### C: Redis Streams / Valkey

* Good, because consumer groups give at-least-once; Redis 8.6+ has idempotent `XADD`.
* Bad, because Redis 8 is tri-licensed (RSALv2/SSPLv1/AGPLv3); Valkey (BSD) may lack idempotent add (unverified); no advantage over NATS here.

### D: RabbitMQ

* Good, because mature, dead-letter exchanges, management UI.
* Bad, because heavier (Erlang VM) and no native deduplication.

### E: Memgraph triggers

* Good, because a `BEFORE COMMIT` trigger is atomic with the write.
* Bad, because external delivery needs a custom Python query module doing network I/O inside the database process; no official example; logic hidden in the DB.

### F: KG outbox

* Good, because atomic with facts, no broker.
* Bad, because polling latency, sequence generation for ordering, and event data in the knowledge graph.

## More Information

* NATS server 2.15.0, Apache-2.0 (research 2026-10-06).
* Related: ADR-0002, ADR-0006.

## Amendments

* 2026-10-06, review decision D-1 (option b): events are the **only** signal the orchestrator chains steps on. Every A2A skill task publishes exactly one completion event (`extraction.completed`, `tracelinks.updated`, `inconsistencies.updated`) with `causationid` = `messageId` of the A2A request. The agent library validates the skill output, then publishes the event (await ack), then completes the task, so an event never precedes a failed output validation. `tracelinks.updated` is published once per task (listing all approach runs) instead of once per approach. A2A polling only detects failures (ADR-0002 amendment). Contract version 0.2.0.
* 2026-10-09, review decision D-8 (R-004): intake via REST returns **201** (completion is synchronous), not 202; "Intake via REST returns 202" above is superseded.
