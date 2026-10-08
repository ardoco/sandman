---
type: concept
title: Status, Logging and Telemetry
description: How SANDMAN services are observed - the StatusTracker and the uniform /healthz, /readyz and GET /v1/status (working/idle) contract, structured JSON logging with the optional SANDMAN_LOG_FILE JSONL sink, OpenTelemetry wiring, and the planned admin dashboard.
tags: [observability, status, logging, opentelemetry, dashboard, operations]
verified:
  - by: openwiki/0.5.2
    at: 2026-10-07T15:57:21.237Z
sources:
  - id: openwiki-source-3660ce0b8ec17d013eb96a85
    resource: repo://adr/0016-system-overview-dashboard.md
  - id: openwiki-source-92f0e271ee7d9e52edd69c2c
    resource: repo://contracts/openapi/shared.yaml
  - id: openwiki-source-39b446fb18e7ddf4812ef534
    resource: repo://deploy/docker-compose.yml
  - id: openwiki-source-00f42dc157aa9288b7154bd7
    resource: repo://libs/java/agent-lib-a2a/src/main/java/edu/kit/kastel/mcse/ardoco/sandman/agent/a2a/A2aServer.java
  - id: openwiki-source-91296d3ae51ada732a75cd2b
    resource: repo://libs/java/agent-lib/src/main/java/edu/kit/kastel/mcse/ardoco/sandman/agent/Event.java
  - id: openwiki-source-90f72d459ec944f85e3d5c9f
    resource: repo://libs/java/agent-lib/src/main/java/edu/kit/kastel/mcse/ardoco/sandman/agent/MdcKeys.java
  - id: openwiki-source-b2244b76095d919c20dcd7ec
    resource: repo://libs/java/agent-lib/src/main/java/edu/kit/kastel/mcse/ardoco/sandman/agent/StatusTracker.java
  - id: openwiki-source-dde25c8ec7387486996a8bfd
    resource: repo://libs/python/sandman-agent/src/sandman_agent/events.py
  - id: openwiki-source-89f9a90deefed5485cd4ea0c
    resource: repo://libs/python/sandman-agent/src/sandman_agent/llm.py
  - id: openwiki-source-aaa542c4c624865ea38bd208
    resource: repo://libs/python/sandman-agent/src/sandman_agent/status.py
  - id: openwiki-source-da8e37da9ead5bccfc0ba3cf
    resource: repo://libs/python/sandman-agent/src/sandman_agent/telemetry.py
  - id: openwiki-source-dc2a7c5a920d7b7bf3a0cd00
    resource: repo://services/llm-replay/src/sandman_llm_replay/app.py
  - id: openwiki-source-0235ce3c8137ab64a972ef47
    resource: repo://services/orchestrator/src/sandman_orchestrator/app.py
  - id: openwiki-source-bfaf1c3841bd3549fa1c83bb
    resource: repo://services/query-service/src/sandman_query/app.py
  - id: openwiki-source-73e3d9bf65ee9492cb7af7da
    resource: repo://services/query-service/src/sandman_query/service.py
generated: { by: "openwiki/0.5.2", at: "2026-10-07T15:57:21.237Z" }
---

# Status, Logging and Telemetry

SANDMAN is observed through three uniform, service-agnostic mechanisms that both agent
libraries implement identically: a **status contract** (liveness, readiness, and
working/idle activity), **structured JSON logging**, and optional **OpenTelemetry tracing**
that propagates `traceparent` into domain events and LLM calls. A dedicated **dashboard**
service (ADR-0016) is wired in compose but arrives in M1.

## The status contract

`contracts/openapi/shared.yaml` defines three reusable path items that every SANDMAN
service exposes:

| Endpoint | Security | Meaning |
|---|---|---|
| `GET /healthz` | none | Liveness: the process is up (`Health` schema, always `ok`) |
| `GET /readyz` | none | Readiness: dependencies reachable; `503` + `Health` with per-dependency `checks` when down |
| `GET /v1/status` | service token | Activity for the system overview: `ServiceStatus` with `state` = `working` iff `activeTasks > 0` |

The `ServiceStatus` schema (`shared.yaml` → `components/schemas/ServiceStatus`) requires
`service`, `version` (image tag + git commit, default `dev` from `SANDMAN_VERSION`),
`state` (`working` | `idle`) and `activeTasks`, and optionally carries `lastActivityAt`
and a free-form `details` object (e.g. the orchestrator's `runningWorkflows` /
`pendingSteps`, llm-replay's `mode` / `experiment` / `hits` / `misses`).

### StatusTracker

Both libraries implement the same `StatusTracker`: it counts **in-flight tasks** and
reports `working` while at least one is active.

- **Python** (`libs/python/sandman-agent/src/sandman_agent/status.py`):
  `track()` is an async context manager that increments/decrements the counter and
  stamps `lastActivityAt` on entry and exit; `snapshot(details)` renders the
  `ServiceStatus` dict. `status_route(tracker, authenticate, details)` builds the
  Starlette `GET /v1/status` route, returning `401` without an accepted token.
  The `serve()` glue in `a2a.py` mounts it next to the Agent Card and JSON-RPC routes.
- **Java** (`libs/java/agent-lib/src/main/java/.../StatusTracker.java`):
  `track()` returns an `Active` handle (AutoCloseable, try-with-resources, closed
  exactly once via `AtomicBoolean`) and `snapshot(details)` renders the JSON object.
  `A2aServer` (`agent-lib-a2a`) registers `GET /v1/status` (401 without token),
  `GET /healthz` and `GET /readyz` as Vert.x routes and produces `StatusTracker` as a
  `@DefaultBean` agents may replace; `SkillExecutor.execute` wraps each skill run in
  `status.track()`.

### QUIET paths: not every request is a task

In the query-service, `Middleware` (`services/query-service/src/sandman_query/app.py`)
wraps every HTTP request in `tracker.track()` - except paths in
`QUIET = {"/healthz", "/readyz", "/v1/status"}` (`services/query-service/src/sandman_query/service.py`),
which pass through uncounted. This keeps the monitoring endpoints from making the
service appear `working` while someone polls it; the dashboard can poll `/v1/status`
without distorting the very value it reads. The query-service also restricts
`GET /v1/status` to services and unrestricted admins (plugins and scoped users get 403).

The orchestrator derives its status differently: instead of `track()` around a block,
`GET /v1/status` (`services/orchestrator/src/sandman_orchestrator/app.py`) pulls
`runningSteps` from its SQLite state and `lastActivityAt` from
`max(updated_at) FROM workflows`, then feeds them into `tracker.snapshot(counts)` as
`details`. llm-replay counts proxied LLM requests in a plain `active` counter and adds
cassette counters (`mode`, `experiment`, `entries`, `hits`, `misses`) as `details`.

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Parse error on line 9: ...aths bypass counting Expecting '()', 'SOLID_OPEN_ARROW', 'DOTTED_OPEN_ARROW', 'SOLID_ARROW', 'SOLID_ARROW_TOP', 'SOLID_ARROW_BOTTOM', 'STICK_ARROW_TOP', 'STICK_ARROW_BOTTOM', 'SOLID_ARROW_TOP_DOTTED', 'SOLID_ARROW_BOTTOM_DOTTED', 'STICK_ARROW_TOP_DOTTED', 'STICK_ARROW_BOTTOM_DOTTED', 'SOLID_ARROW_TOP_REVERSE', 'SOLID_ARROW_BOTTOM_REVERSE', 'STICK_ARROW_TOP_REVERSE', 'STIC -->
```text
sequenceDiagram
    participant Ops as Operator / dashboard
    participant Svc as SANDMAN service
    participant T as StatusTracker
    Ops->>Svc: GET /v1/status (bearer service token)
    Svc->>T: snapshot(details)
    T-->>Svc: service, version, state, activeTasks
    Svc-->>Ops: 200 ServiceStatus JSON (401 without token)
    Note over T: track() wraps A2A skills and REST requests; QUIET paths bypass counting
```

*The /v1/status exchange; a service is `working` iff at least one task is in flight.*

### Compose healthchecks

`deploy/docker-compose.yml` drives readiness from the same endpoints: Python services
use the `*py-health` anchor polling `http://127.0.0.1:8000/readyz`, Java services use
`curl http://127.0.0.1:8080/readyz` with a longer `start_period`. `depends_on:
{ condition: service_healthy }` chains startup (e.g. the orchestrator waits for all
agents; the dashboard deliberately waits **only** on Memgraph and NATS so it can still
render the overview when agents or the query-service are down).

## Structured JSON logging

Python services log through `setup_logging(service, log_file)`
(`libs/python/sandman-agent/src/sandman_agent/telemetry.py`), called in each service's
lifespan startup (e.g. `services/extraction-agent/src/sandman_extraction/app.py`):

- `JsonFormatter` emits one JSON object per line with `ts` (ISO-8601 UTC, ms), `level`,
  `service` (the per-service `SANDMAN_SERVICE_NAME`) and `msg`.
- **Context fields** (`workflowId`, `taskId`, `runId`, `projectId`, `actor`) are held in
  a `contextvars` variable; `telemetry.bind(**fields)` / `reset(token)` merge them into
  every log line of the current task. The A2A executor binds `taskId`, `workflowId` and
  `actor` around each skill execution. In Java, the same names are the SLF4J MDC keys in
  `MdcKeys.java` (`agent-lib`); the log format itself is configured by the applications.
- **Per-record fields** use the `extra={"fields": {...}}` convention across services -
  e.g. the orchestrator's audit entry `extra={"fields": {"audit": dict(event.data)}}`
  on inconsistency status changes, dead-event records
  `{"event": ..., "eventId": ...}`, and the extraction fallback
  `{"plugin": plugin_id}`. The formatter merges any `fields` dict into the line.
- When an OTel span is active, `traceId`/`spanId` are added to every record; exceptions
  render as `error`.

### Optional JSONL file sink

If `SANDMAN_LOG_FILE` is set (compose default empty; e.g. `/logs/sandman.jsonl`),
`setup_logging` adds a `RotatingFileHandler` (50 MB per file, 5 backups) writing the
same JSON lines; the parent directory is created on demand. Every service in
`deploy/docker-compose.yml` mounts the host directory `../data/logs` at `/logs`, so the
file sink lands in the repository's `data/logs` directory when enabled.

## OpenTelemetry

Tracing is opt-in and env-driven (set in the shared `x-agent-env` anchor):

- `OTEL_SDK_DISABLED` (default `true`) — `setup_logging` only installs the OTel SDK when
  it equals `false`.
- `OTEL_EXPORTER_OTLP_ENDPOINT` (e.g. `http://otel-lgtm:4318`) — used by the
  `OTLPSpanExporter` via a `BatchSpanProcessor` with `service.name` = the SANDMAN
  service name.

With the **`obs` profile** (`docker compose --profile obs up`), the compose file runs
`grafana/otel-lgtm:0.35.0` (`otel-lgtm` service) as an OpenTelemetry backend on the
internal network; its Grafana UI is not exposed directly but is published through
**edge-proxy** on `127.0.0.1:3001`.

### traceparent propagation

`telemetry.trace_headers()` injects the W3C `traceparent` header of the current span
(empty without an active span). Two places propagate it:

1. **LLM calls**: `OpenAiLlmClient.chat`/`embed`
   (`libs/python/sandman-agent/src/sandman_agent/llm.py`) pass
   `extra_headers=telemetry.trace_headers()` on every chat completion and embedding
   request, so spans continue into the gateway/replay proxy.
2. **Domain events**: `build_event`
   (`libs/python/sandman-agent/src/sandman_agent/events.py`) stores
   `telemetry.trace_headers().get("traceparent")` in the CloudEvents envelope; the
  `Event` schema (`events.schema.json`, mirrored in Java `Event.java`) carries
  `traceparent` alongside `correlationid`/`causationid`, so a consumer can resume the
  trace when handling an event.

## Dashboard (M1)

Per ADR-0016, the system overview is a **separate `dashboard` service** (P1), for
**global admins only** (A1), server-rendered with vendored Cytoscape.js/uPlot/htmx (G1),
reading cAdvisor in the opt-in `resources` profile (R1). **Its image is not yet in this
repository - it arrives in M1 (task T1.9);** the service is already fully wired in
`deploy/docker-compose.yml` under the `dashboard` compose profile:

- `127.0.0.1:8090` via edge-proxy; its own admin login (`dashboard_admin_password`,
  `dashboard_session_secret`) with session rules like the console.
- `SANDMAN_TARGETS` lists the `/v1/status` URLs of all monitored services
  (extraction-agent, tlr-agent, inconsistency-agent, orchestrator, query-service,
  llm-replay) - this, together with `/readyz`, is the health and activity data source.
- Additional data sources (ADR-0016): Memgraph read transactions (graph view,
  `SHOW STORAGE INFO`), the NATS monitoring endpoint (`SANDMAN_NATS_MONITOR_URL`,
  `/jsz`) and an ephemeral ordered consumer for the event viewer, the orchestrator REST
  API (workflow monitor, dead-event replay, cancel), Bifrost `/metrics` and management
  API with gateway admin credentials, and cAdvisor (`SANDMAN_CADVISOR_URL`, profile
  `resources`). Its own SQLite (`dashboard-data`) keeps 30 s history samples with 14-day
  retention.

The dashboard is deliberately privileged (it holds the Memgraph password, gateway
admin password and a service token) and deliberately does not depend on the components it
monitors, so the overview survives their failures.

## Relationships

- The status endpoints are the dashboard's primary data source (ADR-0016, T1.9–T1.11).
- `SANDMAN_LOG_FILE` and OTEL env variables are part of the shared compose anchor -
  see [/openwiki/operations/configuration.md] and [/openwiki/operations/deployment.md].
- The query-service publishes `/v1/status` like any other service; its console
  counterpart is described in [/openwiki/services/query-service.md]; the overall
  topology is in [/openwiki/architecture/system-overview.md].
