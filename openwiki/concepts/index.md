# Files

- [A2A Skills and Agent Cards](a2a-skills.md) - The SANDMAN skill-invocation convention layered on A2A 1.0 (JSON-RPC binding) — metadata keys, single data part in and out, SkillError reporting, the exactly-one completion-event rule, and the client-side submit/poll/cancel pattern.
- [Authentication, Roles and Trusted Forwarding](auth-and-roles.md) - SANDMAN's trust model — bearer tokens verified against sha256 hashes, the query-service user store (Argon2id, sessions, personal tokens), per-project roles with min(membership, token maxRole), plugin principals, and the trusted-forwarder actor headers.
- [Cypher Guard and NL Querying](cypher-guard.md) - The query-service read path — recursive-descent Cypher guard (allowlisted read subset, $projectId anchoring, statement rebuilding), Reader read-transactions with post-hoc scope checks, timeouts/memory limits, and the regex NL-intent stub.
- [NATS JetStream Event Bus](event-bus.md) - How SANDMAN agents and the orchestrator exchange domain events over NATS JetStream — stream and consumer configuration, the five sandman.* event types with CloudEvents 1.0 envelopes, at-least-once delivery with inbox deduplication, and the dead-events capture/replay path.
- [Inconsistency Lifecycle](inconsistency-lifecycle.md)
- [Plugin System (Extractors and Detectors)](plugins.md)
