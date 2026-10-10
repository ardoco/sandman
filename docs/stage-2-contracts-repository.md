# SANDMAN – Stage 2: Contracts and Repository

Status: revision 4 (Stage 4 review decisions and the system overview dashboard incorporated; contract version 0.5.0) · Date: 2026-10-06 · Builds on [Stage 1](stage-1-analysis-architecture.md) (revision 4) and the [ADRs](../adr/README.md).

## 0. Summary of key decisions

1. **Contracts are files, not prose.** `contracts/` holds JSON Schemas (common types, events, skill I/O incl. plugin skills), A2A Agent Cards, OpenAPI 3.1 documents for every REST API, the KG schema contract and init script, and language-neutral conformance vectors. All files are validated in CI (Section 1.8); stubs and final implementations are tested against the same files.
2. **One contract version (0.5.0) for the whole system**, semantic versioning: additive changes → minor, breaking → major plus new event type/skill id suffix (before 1.0, breaking changes bump the minor version; 0.2.0 applied the review decisions, 0.3.0 added `GET /v1/status`, 0.4.0 added planned operations, config schemas, lifecycle table, idempotency vectors and `Element.references`, 0.5.0 added `Run.mixedModels`, the `SchemaVersion` node and the optional manifest for archive-only uploads (a partial ingestion: no deletions); the KG schema carries its own version, 0.4.0).
3. **Monorepo**: uv workspace (Python 3.13) and Maven multi-module build (Java 21, Quarkus for A2A servers) side by side; root-level contracts; one Compose file.
4. **Docker:** multi-stage images with the repository root as build context (libraries and contracts are copied in); non-root runtime users; the ArDoCo inconsistency branch is built from a pinned commit inside the Java image build.
5. **Reference Compose file** [deploy/docker-compose.yml](../deploy/docker-compose.yml) with three networks (internal without egress, egress, edge with only a reverse proxy), health-gated startup, per-service file secrets, memory limits, the `llm-replay` proxy, profiles `obs`, `local-llm`, `stubs`, and a dev overlay. Validated with `docker compose config`.
6. **Stubs** implement the real contracts, auth, events and lifecycle; only the "intelligence" (extraction depth, TLR, detection, NL→Cypher) is trivial. The **walking skeleton** runs without any LLM provider key and exercises two ingestions, links, detection, triage and auto-resolution.

## 1. Contract-first interfaces

### 1.1 Inventory

| File | Defines | Producer/implementer | Consumer |
|---|---|---|---|
| [contracts/common.schema.json](../contracts/common.schema.json) | ids, enums, `Actor`, `ElementLocator`, cassette format (`llm-replay`), `SkillError`, skill-invocation metadata | both agent libraries, llm-replay | everyone |
| [contracts/events.schema.json](../contracts/events.schema.json) | CloudEvents envelope + 5 event payloads | extraction-, tlr-, inconsistency-agent | orchestrator (+ future consumers) |
| [contracts/skills.schema.json](../contracts/skills.schema.json) | input/output of all A2A skills incl. `sandman.plugin.extract`/`detect` and `Finding` | agents, plugins | orchestrator, extraction-/inconsistency-agent (plugin callers) |
| [contracts/agent-cards/*.json](../contracts/agent-cards/) | A2A 1.0 Agent Cards of the three agents (one skill each) + two plugin templates | agents/plugins serve them at `/.well-known/agent-card.json` | orchestrator, plugin callers, future planner |
| [contracts/openapi/extraction-agent.yaml](../contracts/openapi/extraction-agent.yaml) | project registration, ingestion (check + upload) | extraction-agent | query-service |
| [contracts/openapi/inconsistency-agent.yaml](../contracts/openapi/inconsistency-agent.yaml) | detector catalog, list/detail/transition of inconsistencies | inconsistency-agent | query-service |
| [contracts/openapi/tlr-agent.yaml](../contracts/openapi/tlr-agent.yaml) | approach catalog `GET /v1/approaches` | tlr-agent | orchestrator, query-service, planner |
| [contracts/openapi/plugin-http.yaml](../contracts/openapi/plugin-http.yaml) | plain-HTTP plugin variant: `GET /manifest`, `POST /v1/extract`, `POST /v1/detect` (same payloads as the A2A plugin skills) | plugin services | extraction-/inconsistency-agent |
| [contracts/openapi/orchestrator.yaml](../contracts/openapi/orchestrator.yaml) | workflows, manual triggers, dead events | orchestrator | query-service, operators |
| [contracts/openapi/query-service.yaml](../contracts/openapi/query-service.yaml) | user/agent entry point: auth, admin, projects, forwarded operations, query, schema, export; minimum role per operation (`x-sandman-min-role`) | query-service | users, CLI/CI, agents, detector plugins |
| [contracts/openapi/shared.yaml](../contracts/openapi/shared.yaml) | problem details, security schemes, shared parameters/responses, `ServiceStatus` + `GET /v1/status` (working/idle), health endpoints | – | all OpenAPI documents |
| [contracts/kg/schema.yaml](../contracts/kg/schema.yaml) | node labels, properties, relationships, owners, dialect notes | writers | contract tests, NL-to-Cypher prompt, `GET /v1/schema` |
| [contracts/kg/init.cypher](../contracts/kg/init.cypher) | constraints and indexes, `SchemaVersion` | `kg-migrate` service (owners keep an idempotent `init_schema`) | – |
| [contracts/conformance/vectors.json](../contracts/conformance/vectors.json) | expected values for `stableId`, sentence keys, fingerprints, cassette keys (RFC 8785, llm-replay), skill invocation, event validation | both libraries, llm-replay | CI |

### 1.2 Conventions

- **Versioning:** contract version `0.5.0` in schema descriptions and OpenAPI `info.version`. Payload changes are additive only within a major version; consumers ignore unknown members where the schema allows it (outputs) and producers never send unknown members (`additionalProperties: false` on inputs).
- **Ids:** `stableId(prefix, parts…)` (deterministic, see vectors) for everything that must be idempotent; `run_`/`wf_` ids are random. Element keys `artifactId#localKey` are stable across versions.
- **Errors:** REST → RFC 9457 problem details; A2A → task state `TASK_STATE_FAILED`/`TASK_STATE_REJECTED` with `SkillError` in status message metadata `sandman.error` (`code`, `message`, `retryable`).
- **Auth headers:** `Authorization: Bearer <service token>` between services; each service holds only its own token, receivers verify against hashes. User identity only via `X-Sandman-Actor` from the query-service and via `sandman.actor` A2A metadata from the orchestrator (trusted forwarders). Owners reject these headers from other callers with 403. Owners call plugins with the plugin's inbound token.
- **Time:** ISO-8601 UTC strings everywhere.

### 1.3 A2A skill invocation (SANDMAN convention on A2A 1.0, JSON-RPC binding)

Request from the orchestrator (non-blocking):

```json
{
  "jsonrpc": "2.0", "id": "1", "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "0b6f4a0e-8f6e-4c6b-9d7a-1f2e3d4c5b6a",
      "role": "ROLE_USER",
      "metadata": {
        "sandman.skill": "recover-trace-links",
        "sandman.workflowId": "wf_0123456789abcdef0123456789abcdef",
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
      },
      "parts": [ { "data": { "projectId": "mediastore",
                             "sourceVersionIds": ["ver_0123456789abcdef0123456789abcdef"],
                             "targetVersionIds": ["ver_fedcba9876543210fedcba9876543210"] } } ]
    },
    "configuration": { "returnImmediately": true }
  }
}
```

Completion: the step completes when the task's completion event arrives (`causationid` = `messageId` of the A2A request, Section 1.4). `GetTask {"id": "<taskId>"}` polling (30 s → 2 min) only detects `_FAILED`, `_CANCELED`, `_REJECTED`, lost tasks and timeouts. Result: `task.artifacts[]` contains exactly one artifact with `name: "result"` and one `data` part valid against the skill's output schema (referenced from the card extension `https://sandman.invalid/a2a/extensions/skill-invocation/v1`). Field names follow the A2A 1.0 proto JSON mapping; exact spelling of `returnImmediately` and the JSON form of `securitySchemes` are checked against the pinned SDKs in M0 (V-5, V-19).

Behavior every agent library implements identically (conformance vectors `skillInvocation`):
- missing `sandman.skill` or schema-invalid input → `TASK_STATE_REJECTED`, code `INVALID_INPUT`; unknown skill → `REJECTED`, `UNKNOWN_SKILL`;
- output that violates the output schema → `FAILED`, code `INTERNAL`, `retryable=false` (bug, not transient); no event is published;
- valid output → the library publishes the skill's single completion event (await ack), then sets `COMPLETED` with the result artifact;
- `CancelTask` sets a cancellation flag visible in `SkillContext`; long loops check it.

### 1.4 Events

- Stream (created idempotently by the library on startup of any publisher/consumer):

```json
{ "name": "SANDMAN", "subjects": ["sandman.>"], "storage": "file", "retention": "limits",
  "max_age": "90d", "duplicate_window": "2m", "discard": "old", "num_replicas": 1 }
```

- Orchestrator consumer: durable `orchestrator`, `filter_subject: sandman.>`, `ack_policy: explicit`, `ack_wait: 60s`, `max_deliver: 5`, `backoff: [5s, 30s, 120s, 300s]`, `max_ack_pending: 100`.
- Event types, producers and triggers:

| Type (= NATS subject) | Producer | Published when | Orchestrator reaction (rules.yaml) |
|---|---|---|---|
| `sandman.ingestion.completed` | extraction-agent | after an ingestion's KG commit, only if the state changed | extract created and reactivated versions; detection on deleted artifacts if only deletions |
| `sandman.extraction.completed` | extraction-agent | completion event of the extraction task | step done; debounced TLR for changed × counterpart versions, or detection if no TLR kind pair applies |
| `sandman.tracelinks.updated` | tlr-agent | completion event of the TLR task (all approach runs) | step done; debounced detection on the artifacts of the source and target versions |
| `sandman.inconsistencies.updated` | inconsistency-agent | completion event of the detection task | step done; close workflow |
| `sandman.inconsistency.status.changed` | inconsistency-agent | user transitions only (system transitions are summarized in `inconsistencies.updated`) | audit log |

### 1.5 REST APIs and authorization

The OpenAPI files are normative. Authorization matrix (enforced by the query-service; owners trust it):

| Operation (query-service) | viewer | triager | maintainer | admin (global) | service/plugin token |
|---|---|---|---|---|---|
| list projects, `GET /v1/schema`, NL query, scoped raw Cypher, exports, read inconsistencies/workflows | ✓ | ✓ | ✓ | ✓ | ✓ (plugins: read-only) |
| inconsistency transitions | – | ✓ | ✓ | ✓ | – (users only) |
| ingestion (check, upload), workflow trigger | – | – | ✓ | ✓ | CI uses a personal token |
| unscoped raw Cypher | – | – | – | ✓ | – |
| users, projects, memberships | – | – | – | ✓ | – |

All reads are confined to one project by the scope guard (Stage 1 §7); members see only their projects. Orchestrator workflow cancel and dead-event replay are also accepted from the `dashboard` service token (admins, ADR-0016). Plugin callers (`plugin:<id>`) are rejected by every receiver except the query-service, which allows them scoped reads on the projects granted in `config/query/plugins.yaml`.

### 1.6 Configuration file contracts

Mounted read-only at `/config` (repository folder `config/`); the effective file hash is stored in `Run.configHash`, except for `config/tlr/approaches.yaml`: there `configHash` is the sha256 of the JCS of one approach's effective params (T0.6), so enabling another approach does not change existing link ids. Schemas for these files are added to `contracts/config/` in M0 together with the first implementation (formats below are binding).

```yaml
# config/llm/models.yaml – logical routes → gateway model names (all LLM users)
routes:
  chat-default: openai/<pinned-model>
  chat-alt: openrouter/<vendor>/<model>
  chat-local: ollama/<model>:<tag>
  embed-default: openai/<embedding-model>
  embed-local: ollama/<embedding-model>
openrouterProviders:        # optional: pinned upstream provider per OpenRouter model (llm-replay)
  <vendor>/<model>: <provider>
```

```yaml
# config/orchestrator/rules.yaml
debounceSeconds: 10
maxConcurrentPerAgent: 2
timeouts: { extract-artifact-versions: 15m, recover-trace-links: 60m, detect-inconsistencies: 30m }
poll: { initial: 30s, max: 2m }   # GetTask only detects failures; steps complete on their event
retry: { maxAttempts: 3, backoff: [30s, 2m, 10m] }
tlr:
  kindPairs:                     # which pairs to link after changes; approaches per pair
    - { source: requirements, target: code, approaches: [stub-name-match] }
    - { source: architecture-doc, target: architecture-model, approaches: [stub-name-match] }
```

```yaml
# config/extraction/plugins.yaml
plugins:
  - id: code-extractor
    transport: a2a                        # a2a | http (extraction-agent implements a2a only until needed)
    url: http://plugin-stubs:8000          # a2a: Agent Card at /.well-known/agent-card.json; http: /manifest
    skill: sandman.plugin.extract
    tokenFile: /run/secrets/plugin_in_plugin-stubs   # inbound token of the plugin
    match: { kinds: [code, test] }        # or formats / globs
    timeout: 10m
explicitReferences:
  patterns: ['REQ-\d+', 'FR-\d+']
```

```yaml
# config/tlr/approaches.yaml
approaches:
  stub-name-match: { enabled: true, params: { minTokenLength: 4, confidence: 0.5 } }
  lissa: { enabled: false, params: { retrieval: { k: 10 }, model: chat-default, embed: embed-default } }
  ardoco: { enabled: false, params: {} }
```

```yaml
# config/inconsistency/plugins.yaml
detectors:
  stub-untraced: { enabled: true }
  ardoco-id: { enabled: false, params: { textKinds: [architecture-doc], modelKinds: [architecture-model], linkApproaches: all } }   # Q-20, ADR-0015
plugins:
  - id: detect-stub
    transport: http                       # plain-HTTP variant: POST /v1/detect (inconsistency-agent implements http only until needed)
    url: http://plugin-stubs:8001
    tokenFile: /run/secrets/plugin_in_plugin-stubs
    timeout: 10m
```

### 1.7 Runtime contracts of the libraries (environment)

| Variable | Meaning |
|---|---|
| `SANDMAN_SERVICE_NAME` | caller name (logs, `source` of events, token lookup) |
| `SANDMAN_LLM_BASE_URL` (the `llm-replay` proxy), `SANDMAN_LLM_KEY_FILE`, `SANDMAN_LLM_LOCAL_KEY_FILE`, `SANDMAN_LLM_MODELS_FILE` | LLM access (local key for `local-only` projects) |
| `SANDMAN_LLM_MODE` (`live`/`record`/`replay`) | all agents (`Run.replayMode`) and `llm-replay` (behavior) |
| `SANDMAN_LLM_RECORDS_DIR`, `SANDMAN_LLM_UPSTREAM` | `llm-replay` only |
| `SANDMAN_KG_URI`, `SANDMAN_KG_USER`, `SANDMAN_KG_PASSWORD_FILE` | Memgraph |
| `SANDMAN_NATS_URL` | event bus |
| `SANDMAN_SERVICE_TOKEN_FILE`, `SANDMAN_SERVICE_VERIFY_FILE` | own token; `{callerName: sha256(token)}` for verifying callers |
| `SANDMAN_PLUGIN_INBOUND_TOKEN_FILE` | plugins only: token owners present when calling the plugin |
| `SANDMAN_DASHBOARD_ADMIN_PASSWORD_FILE`, `SANDMAN_SESSION_SECRET_FILE`, `SANDMAN_TARGETS` (`name=url,…` of services with `/v1/status`), `SANDMAN_NATS_MONITOR_URL`, `SANDMAN_GATEWAY_URL`, `SANDMAN_GATEWAY_ADMIN_PASSWORD_FILE`, `SANDMAN_CADVISOR_URL` (optional), `SANDMAN_STATE_DB` | dashboard only |
| `SANDMAN_ARTIFACT_STORE`, `SANDMAN_CONFIG_DIR`, `SANDMAN_LOG_FILE` | paths |
| `OTEL_*` | standard OpenTelemetry SDK variables |

Log line fields (JSON): `ts, level, service, msg, event?, traceId, spanId, workflowId, taskId, runId, projectId, actor`.

### 1.8 Validation status

Validated on 2026-10-06 (commands become the CI `contracts` job):
- JSON Schemas pass the 2020-12 meta-schema; event examples and skill examples in `vectors.json` validate as expected (Python `jsonschema` + `referencing`).
- Cassette keys in `vectors.json` were cross-checked against the `rfc8785` reference implementation.
- All Agent Card extension references resolve to existing `$defs`.
- All OpenAPI files pass `openapi-spec-validator`.
- `docker compose config` succeeds for the base file, the dev overlay and all profiles (with generated secrets).

## 2. Repository

### 2.1 Layout (monorepo, ADR-0008)

```text
sandman/
├── adr/                                ADRs (MADR 4)
├── docs/                               stage documents 1–4 (stage-4-review.md), task specs, notes
├── contracts/                          (Section 1) + config/ schemas from M0
├── libs/
│   ├── python/sandman-agent/           package sandman_agent (+ tests/)
│   ├── java/
│   │   ├── agent-lib/                  plain Java 21
│   │   └── agent-lib-a2a/              Quarkus/CDI glue for a2a-java
│   ├── typescript/sandman-agent/       agent kit, when the first TS agent is planned (ADR-0017)
│   ├── go/sandman-agent/               agent kit, when the first Go agent is planned (ADR-0017)
│   └── rust/sandman-agent/             agent kit, tier 2 (ADR-0017)
├── services/
│   ├── orchestrator/                   Python (FastAPI app + rule engine + SQLite)
│   ├── llm-replay/                     Python (OpenAI-compatible record/replay proxy)
│   ├── dashboard/                      Python (system overview for admins, ADR-0016)
│   ├── extraction-agent/               Python (A2A server + intake REST)
│   ├── query-service/                  Python (FastAPI + Jinja2 console + user store)
│   ├── tlr-agent/                      Java (Quarkus app; approaches as packages)
│   └── inconsistency-agent/            Java (Quarkus app; detectors as packages)
├── tools/cli/                          `sandman` CLI (Python; ingest, workflows, export)
├── tests/
│   ├── conformance/                    runners calling both libraries on contracts/conformance
│   ├── plugin-stubs/                   Python stub service for both plugin skills
│   └── e2e/                            walking skeleton + fixtures/mini/
├── config/                             default runtime configuration (mounted at /config)
├── deploy/                             docker-compose*.yml, gateway/config.json, gen-secrets.sh, .env.example
├── experiments/                        experiment manifests
├── pyproject.toml                      uv workspace root
├── pom.xml                             Maven aggregator
└── Makefile                            developer entry points
```

### 2.2 Build tooling and dependency management

| Concern | Python | Java |
|---|---|---|
| Version | 3.13 | 21 (ArDoCo target) |
| Build/deps (binding: Python = uv only, Java = Maven only; Maven groupId `io.github.ardoco`, artifactIds `sandman-*`, Java packages `edu.kit.kastel.mcse.ardoco.sandman.*`) | uv workspace (`[tool.uv.workspace] members = ["libs/python/*", "services/orchestrator", "services/extraction-agent", "services/query-service", "services/llm-replay", "tools/cli", "tests/plugin-stubs"]`), single `uv.lock` | Maven aggregator; `dependencyManagement` with BOMs (Quarkus platform, OpenTelemetry), properties for a2a-java, neo4j-java-driver, jnats, networknt validator |
| Key libraries | `a2a-sdk[http-server]`, `openai`, `neo4j`, `nats-py`, `pydantic`, `fastapi`, `jinja2`, `jsonschema`, `rfc8785`, `argon2-cffi`, `opentelemetry-sdk`; extraction: `docling` | `a2a-java` reference JSON-RPC server + client (provisional, T0.4), Quarkus (REST, health), `neo4j-java-driver`, `jnats`, `json-schema-validator`, `java-json-canonicalization` (JCS); ArDoCo modules |
| Lint/format | `ruff format`, `ruff check`, `mypy --strict` for `libs/` | Spotless (formatter configuration aligned with ArDoCo), compiler `-Werror` for libs |
| Tests | `pytest`, Testcontainers (`testcontainers[neo4j]` image override to Memgraph, NATS container) | JUnit 5, Testcontainers, Quarkus test |
| Framework guard | CI check: no imports of `langchain*`, `langgraph`, `deepagents`, `llama_index`, `crewai`, `autogen` under `libs/` and `services/` | `maven-enforcer` `bannedDependencies` for agent frameworks; `dev.langchain4j:*` allowed only as transitive dependency of the ArDoCo/LiSSA adapter packages (Q-2) |
| Updates | Renovate (grouped weekly; A2A SDKs pinned, manual) | same |

Further languages (ADR-0017, on demand): TypeScript on Node.js LTS with npm workspaces (`package-lock.json`), Go modules, Rust Cargo workspace (`Cargo.lock`); each kit has its own CI job (lint, tests, conformance vectors, Testcontainers) and a framework guard (no LangChain.js/LangGraph.js/LlamaIndex/Mastra, LangChainGo/Eino, langchain-rust/rig).

ArDoCo dependency: released artifacts (`io.github.ardoco:*`, 2.0.1) from Maven Central for TLR; the inconsistency-detection branch `feature/make-inconsistency-great-again` is built from a pinned commit (`0d1f22b…`) in one pass (`spikes/T0.12/bootstrap.sh`) — in the Docker build stage and via `make ardoco` for local development — with `named-architecture-entity-recognition` 2.1.0 from Maven Central (the script patches the branch's two `2.1.0-SNAPSHOT` references until the branch switches upstream).

### 2.3 CI outline (GitHub Actions)

| Job | Trigger (path filter) | Steps |
|---|---|---|
| `contracts` | `contracts/**` | meta-schema validation, vectors self-check (JCS against `rfc8785`), OpenAPI validation, Agent Card checks |
| `python` | `libs/python/**`, Python services, `contracts/**` | `uv sync --frozen`, ruff, mypy, pytest (unit + Testcontainers), conformance vectors, framework guard |
| `java` | `libs/java/**`, Java services, `contracts/**` | `mvn -B verify` (Spotless check, enforcer, unit, Testcontainers), conformance vectors |
| `compose` | `deploy/**`, `services/**` | `gen-secrets.sh` in temp dir, `docker compose config -q`, build all images |
| `e2e` | PRs touching `services/**` or `contracts/**`, nightly | build, `docker compose --profile stubs up`, run walking skeleton test, collect logs |

## 3. Docker and Compose

### 3.1 Dockerfile strategy

Build context is the repository root for all services (libraries, contracts and config schemas are copied in). `.dockerignore` excludes `data/`, `deploy/secrets/`, build outputs.

Python services (example: extraction-agent):

```dockerfile
FROM python:3.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:<pinned-uv-version> /uv /uvx /bin/
WORKDIR /src
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY libs/python libs/python
COPY services/extraction-agent services/extraction-agent
RUN uv sync --frozen --no-dev --package sandman-extraction-agent
# build-time prefetch of models/parsers so the container runs without egress (V-13)
RUN .venv/bin/python -m sandman_extraction.prefetch

FROM python:3.13-slim-bookworm
RUN useradd --uid 10001 --create-home sandman
WORKDIR /app
COPY --from=build /src/.venv /app/.venv
COPY --from=build /root/.cache/sandman-models /home/sandman/.cache/sandman-models
COPY contracts /contracts
ENV PATH=/app/.venv/bin:$PATH SANDMAN_CONTRACTS_DIR=/contracts
USER sandman
EXPOSE 8000
CMD ["uvicorn", "sandman_extraction.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

Java services (example: inconsistency-agent):

```dockerfile
FROM maven:3.9-eclipse-temurin-21 AS ardoco
COPY spikes/T0.12/bootstrap.sh /bootstrap.sh
# one-pass build of the pinned ArDoCo branch against named-architecture-entity-recognition 2.1.0 (Maven Central);
# pinned commit and Maven repository location are set in the script (adapted for the image in T0.11)
RUN sh /bootstrap.sh

FROM maven:3.9-eclipse-temurin-21 AS build
COPY --from=ardoco /root/.m2 /root/.m2
WORKDIR /src
COPY pom.xml .
COPY libs/java libs/java
COPY services/inconsistency-agent services/inconsistency-agent
COPY contracts contracts
RUN mvn -B -q -pl services/inconsistency-agent -am package -DskipTests

FROM eclipse-temurin:21-jre
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 sandman
COPY --from=build /src/services/inconsistency-agent/target/quarkus-app /app
COPY contracts /contracts
ENV SANDMAN_CONTRACTS_DIR=/contracts
USER sandman
EXPOSE 8080
CMD ["java", "-jar", "/app/quarkus-run.jar"]
```

The tlr-agent image is analogous (ArDoCo stage only if it needs unreleased modules; LiSSA built from source because it is not on Maven Central). The stub images use the same Dockerfiles — stubs are the M0 state of the real services, not separate images.

### 3.2 Reference Compose file

[deploy/docker-compose.yml](../deploy/docker-compose.yml), [deploy/docker-compose.dev.yml](../deploy/docker-compose.dev.yml), [deploy/gateway/config.json](../deploy/gateway/config.json), [deploy/gen-secrets.sh](../deploy/gen-secrets.sh), [deploy/.env.example](../deploy/.env.example).

- **Networks:** `internal` (`internal: true`, all SANDMAN services), `egress` (gateway incl. its admin port; reaches the remote Ollama), `edge` (only `edge-proxy`, a Caddy 2.11 reverse proxy publishing query-service `127.0.0.1:8080`, dashboard `127.0.0.1:8090` and Grafana `127.0.0.1:3001` from the internal network; the published services themselves have no egress — disabling IP masquerading does not block egress on Docker Desktop, spike T0.4 check 13).
- **Startup order:** `memgraph`, `nats`, `llm-gateway` healthy → `llm-replay` → agents healthy (`/readyz`) → `orchestrator` → `query-service`.
- **Healthchecks:** Memgraph via `mgconsole`; NATS `/healthz?js-enabled-only=true` (monitoring port enabled by `-m 8222`); Bifrost `/health`; Python services via `python -c urllib…/readyz`; Java via `curl /readyz`; otel-lgtm built-in.
- **Secrets:** file secrets for all SANDMAN services; each service mounts only its own token (`svc_token_<name>`), the hash file `svc_tokens_verify`, its LLM keys and, for plugin callers, the plugin's inbound token. Two deliberate exceptions, because the images read credentials from environment variables only: `secrets/gateway.env` (provider keys, Bifrost encryption key, admin password, virtual key values `VK_*`) and `secrets/memgraph.env` (`MEMGRAPH_USER`, `MEMGRAPH_PASSWORD`). `gen-secrets.sh` creates matching values (each `llm_key_<component>[_local]` equals its `VK_*`; virtual keys carry Bifrost's required `sk-bf-` prefix; plugin token pairs for the names in `PLUGINS`).
- **Gateway:** Bifrost `v2.2.6`; `config.json` declares providers (OpenAI, OpenRouter, remote self-hosted Ollama via `SELFHOSTED_LLM_URL`/`SELFHOSTED_LLM_KEY`), admin authentication (`governance.auth_config`, password from `BIFROST_ADMIN_PASSWORD`), one virtual key per component plus `experiments`, an Ollama-only `<component>-local` key per LLM-using component, budgets (USD/month placeholders) and rate limits; `enforce_auth_on_inference: true`; no fallbacks. Clients authenticate with `Authorization: Bearer sk-bf-…` against base URL `http://llm-replay:8000/openai`, which forwards to `http://llm-gateway:8080/openai` (pass-through in `live` mode). Governance is part of the open-source build (no license check in `server/plugins.go`, per source reading) — runtime confirmation in the M0 smoke test.
- **Memgraph flags:** telemetry off, `LOAD CSV` disabled, schema info enabled (NL-to-Cypher), 300 s execution timeout (agent batch writes), snapshots every 300 s, `--memory-limit=8192` (MiB, below the container limit; V-30), logs to stderr.
- **Memory budget** (`mem_limit`, 32 GB host per A-7; initial values, tuned in T5.4; JVM heaps are 75 % of their container limit):

| Service | Limit | Service | Limit |
|---|---|---|---|
| memgraph | 10 GB (engine 8 GB) | extraction-agent | 3 GB |
| tlr-agent | 5 GB | inconsistency-agent | 5 GB |
| query-service | 1 GB | llm-gateway | 1 GB |
| orchestrator, llm-replay, nats, plugin-stubs, dashboard | 0.5 GB each | otel-lgtm (obs) | 2 GB |
| edge-proxy | 0.125 GB | | |
| cadvisor (resources) | 0.25 GB | | |

  Total core ≈ 27 GB (incl. dashboard); Ollama runs on a remote server and is not sized here. Memgraph memory is monitored with `SHOW STORAGE INFO`.
- **Dashboard** (ADR-0016): port `127.0.0.1:8090` via `edge-proxy`, network `internal` only, depends only on Memgraph and NATS (not on the services it monitors), secrets `svc_token_dashboard`, `dashboard_admin_password`, `dashboard_session_secret`, `memgraph_password`, `gateway_admin_password`; volume `dashboard-data`. cAdvisor runs in the opt-in profile `resources` with read-only host mounts (V-37).
- **Plugins:** real plugin services are added via `docker-compose.override.yml` (template comment in the base file); the `stubs` profile starts `plugin-stubs` for the walking skeleton.

### 3.3 Local development workflow

```sh
make secrets                    # deploy/gen-secrets.sh; then add provider keys to deploy/secrets/gateway.env
make infra                      # compose (base + dev overlay) up: memgraph, nats, llm-gateway
# run one service on the host against the infra (env from deploy/dev.env, ports 7687/4222/8081 on localhost)
uv run --package sandman-query-service uvicorn sandman_query.app:app --reload
mvn -pl services/tlr-agent -am quarkus:dev
make test                       # uv run pytest + mvn verify (Testcontainers; no compose needed)
make up                         # whole system (images built)
make e2e                        # compose --profile stubs up + walking skeleton test
make logs / make down / make backup
```

Recording LLM interactions for an experiment: `SANDMAN_EXPERIMENT=<name> SANDMAN_LLM_MODE=record make up` (read by `llm-replay`); replay with `SANDMAN_LLM_MODE=replay`.

## 4. Stub specification

General rules for all stubs: real HTTP/A2A servers serving the real Agent Cards and OpenAPI contracts, including `GET /v1/status` (from the libraries); real auth (service tokens, trusted forwarder); real KG writes with provenance (`Run` nodes, `tool = stub-*`, `toolVersion = 0`); real events; health/readiness endpoints; no LLM calls. A stub becomes "finalized" when it meets the criteria below — contract tests stay unchanged in between.

### 4.1 Orchestrator

| | |
|---|---|
| Stub behavior | Complete rule engine for the rules in `config/orchestrator/rules.yaml`, SQLite state, inbox dedupe, debounce, per-project pipeline lock, A2A submit, step completion by event, `GetTask` polling for failures, retries, timeouts, dead events, REST API. No planner (`/v1/requests` absent). |
| Configuration | `rules.yaml`, `SANDMAN_AGENTS` (agent URLs). |
| Finalized when | (1) all rule unit tests and failure-injection tests (agent killed mid-task, NATS restart, duplicate events) pass; (2) a redelivered or duplicated event creates no new steps (`actionKey` includes the trigger id); (3) optional planner behind feature flag with approval gate (later milestone, separate criterion). |

### 4.2 Extraction agent

| | |
|---|---|
| Stub behavior | Full intake (check, upload, content-addressed store, versions, deletions, `ingestion.completed`). Extraction: one `Element` per file (label by kind: `TextUnit`/`ModelElement`/`CodeUnit`/`TestCase`/`Requirement`, `kind=file`, `text` = first 500 chars); the file-level element is never a `Requirement` (`TextUnit` for `requirements`, `localKey` `file`); for `requirements` Markdown additionally one `Requirement` per heading `## <ID> <title>` (`localKey` = ID, `name` = title); routes artifacts matching `plugins.yaml` to plugins (A2A) and writes their elements/relations; explicit-reference regex matcher (cheap, real). |
| Configuration | `config/extraction/plugins.yaml`; fixtures in `tests/e2e/fixtures/`. |
| Finalized when | Built-in extractors for Markdown/text (sections, sentences with `sentenceIndex`), PDF/DOCX (Docling), requirements CSV/Markdown, PCM and UML models (ids preserved) pass golden-file tests on the ArDoCo benchmark projects' artifacts; LLM enrichment validated against schemas and covered by replay tests; incremental ingestion of 1 000 files < 60 s (excluding plugins). |

### 4.3 TLR agent

| | |
|---|---|
| Stub behavior | Approach `stub-name-match`: tokens of element names (split on camelCase/punctuation, lowercase, length ≥ `minTokenLength`); link when source and target share a token; `confidence` from config; `linkType = relates`. `GET /v1/approaches` returns the enabled approaches. Deterministic ids, `Run` per approach; links written inactive and activated in each run's final transaction; one `tracelinks.updated` per task listing all runs. |
| Configuration | `config/tlr/approaches.yaml`. |
| Finalized when | ArDoCo and LiSSA approaches run in-process through `llm-replay` and the gateway and pass the reproduction protocol of Stage 3 §3.3 on at least two benchmark projects (ArDoCo: link set identical to the standalone runner; LiSSA: live re-run, Δ F1 with 95 % CI reported, then recorded); per-approach failure isolation tested; link carry-over optional. |

### 4.4 Inconsistency agent

| | |
|---|---|
| Stub behavior | Detector `stub-untraced`: each current `Requirement` without an active, non-rejected link to a current element → finding `untraced-requirement` (severity `warning`, involves the requirement). Calls configured detector plugins. **Real** fingerprinting, re-detection algorithm, lifecycle state machine, triage API, notification log events. |
| Configuration | `config/inconsistency/plugins.yaml`. |
| Finalized when | ArDoCo detectors (`InconsistencyDetection`, `ArtemisInconsistencyDetection`) mapped to KG elements (V-16/V-17) and reproducing ArDoCo's own results on its benchmark (same findings as the standalone runner on the same inputs); state machine property tests (every allowed/forbidden transition); plugin failure never auto-resolves. |

### 4.5 Query service

| | |
|---|---|
| Stub behavior | Real: auth (users, roles, tokens), admin endpoints, forwarding, raw Cypher with validation and read transactions, schema endpoint, exports, console pages. Stubbed: NL endpoint maps 5 regex intents (`untraced requirements`, `links of <name>`, `open inconsistencies`, `components without documentation`, `what changed since <revision>`) to fixed Cypher templates; other questions → 422 with explanation. |
| Configuration | `config/query/intents.yaml`, `examples.yaml` (empty in stub). |
| Finalized when | LLM NL-to-Cypher with validation/repair reaches ≥ 80 % execution accuracy on the held-out example split (target to be confirmed in Stage 3); security test suite (write/admin queries rejected; read transaction blocks writes with validation disabled) green; authorization matrix tests green. |

### 4.6 Plugin stubs (`tests/plugin-stubs`, profile `stubs`)

| Skill | Stub behavior |
|---|---|
| `sandman.plugin.extract` (A2A, port 8000) | For each code/test file: one `CodeUnit`/`TestCase` per `class <Name>` match (regex), `parentLocalKey` = file; `DEPENDS_ON` for `import` lines resolving to known classes; explicit references for `REQ-\d+` in comments. |
| `/v1/detect` (plain HTTP, port 8001) | Returns no findings, but queries `MATCH (e:Element {projectId: $projectId}) RETURN count(e)` through the query-service to prove the read path (count logged and returned as warning text). |

The stubs deliberately use both plugin transports so the walking skeleton covers both. They are test fixtures, not components to finalize; real plugins replace them via `config/*/plugins.yaml`.

### 4.7 LLM gateway, Memgraph, NATS

Real off-the-shelf components from M0. The walking skeleton makes no LLM calls; the gateway smoke test (ADR-0004) runs separately with provider keys.

## 5. Walking skeleton

Fixture project `tests/e2e/fixtures/mini/` (two snapshots):

| Path | Snapshot 1 | Snapshot 2 |
|---|---|---|
| `requirements/requirements.md` | `## REQ-1 Cache media files`, `## REQ-2 Upload audio files`, `## REQ-3 Encrypt user passwords` | REQ-2 renamed to `## REQ-2 Upload audio into cache` |
| `docs/architecture.md` | mentions MediaCache, UserDB | unchanged |
| `docs/old-notes.md` | present | **removed** |
| `model/mini.repository` | PCM repository with components `MediaCache`, `UserDB` | unchanged |
| `src/main/java/mini/MediaCache.java` | class `MediaCache` | unchanged |
| `src/test/java/mini/MediaCacheTest.java` | class `MediaCacheTest`, comment `// REQ-1` | unchanged |

Scenario (`tests/e2e/test_walking_skeleton.py`, against `docker compose --profile stubs up`, all via the query-service API):

1. Admin logs in (bootstrap password), creates project `mini` (`llmPolicy=local-only`), users `alice` (maintainer), `bob` (triager) and `carol` (viewer); alice creates a project token.
2. CLI with alice's token: `sandman ingest --project mini --mode full --revision s1 tests/e2e/fixtures/mini/s1` → `ingestion.completed` with 6 created versions.
3. Wait for the workflow (`GET /v1/projects/mini/workflows`) to reach `SUCCEEDED` with steps extract → tlr → detect.
4. Assert KG via raw Cypher (admin): `Requirement` REQ-1..3; `CodeUnit` `MediaCache` from the plugin stub (`Run.tool = plugin-stubs@…`); explicit link REQ-1 → `MediaCacheTest` (`approach = explicit-reference`); name-match link REQ-1 (`cache`) → `MediaCache`.
5. Assert inconsistencies: `untraced-requirement` for REQ-2 (I-a) and REQ-3 (I-b), both `OPEN`; a `notification` log line exists for each.
6. bob transitions I-b to `DEFERRED` with reason; `inconsistency.status.changed` is published; the same transition attempted by carol (viewer) fails with 403, and a stale `expectedStatus` fails with 409. Read isolation: carol's raw Cypher on `mini` anchored with `{projectId: $projectId}` succeeds; an unanchored query (`MATCH (r:Requirement) RETURN r`) fails with 422; a query for another project fails with 403.
7. CLI: ingest snapshot 2 (`--mode full --revision s2`) → 1 created version, `docs/old-notes.md` marked deleted. Re-sending the same snapshot changes nothing (new `Ingestion` with empty lists, no event).
8. After the workflow: I-a is `RESOLVED` with `verification=confirmed` (REQ-2 now shares token `cache`), I-b stays `DEFERRED` with a new `lastRunId`, history contains the system transition with actor `system:stub-untraced`.
9. Revert: ingest snapshot 1 again (`--mode full --revision s1b`) → `reactivatedVersionIds` contains the s1 versions of `requirements.md` and `docs/old-notes.md` (its `deletedAt` cleared), no created versions; after the workflow I-a is `REOPENED` (REQ-2 untraced again, same fingerprint).
10. Exports: `GET /v1/projects/mini/export/tracelinks?format=csv` contains the expected rows; the detect plugin stub's warning shows a non-zero element count.
11. Failure path: `docker compose kill tlr-agent` during step 7's TLR step → orchestrator retries after restart; final state identical to step 8.

Acceptance: test green in CI in < 5 min on a 4-core runner; no provider keys present; all services report ready; no dead events.

## 6. Assumptions

| ID | Assumption |
|---|---|
| A2-1 | A2A 1.0 JSON field names follow the proto JSON mapping used in the Agent Cards (`supportedInterfaces`, `protocolBinding`, `securitySchemes.bearer.httpAuthSecurityScheme`, `securityRequirements[].schemes`); corrected in M0 if the SDKs serialize differently. |
| A2-2 | `.invalid` URIs (`https://sandman.invalid/...`) are used as schema/extension identifiers; no network resolution is needed (libraries load schemas from `/contracts`). |
| A2-3 | Bifrost's OpenAI-SDK route `/openai` accepts the OpenAI Python and Java SDKs including tool calls and embeddings. |
| A2-4 | Python services listen on 8000, Java services on 8080 inside containers. |
| A2-5 | GitHub Actions is the CI system (repository hosting on GitHub, like ArDoCo). |
| A2-6 | The ArDoCo branch can be built with `mvn install -DskipTests` at the pinned commit. |
| A2-7 | Budget and rate-limit numbers in `gateway/config.json` are placeholders. |

## 7. To verify

| # | Item |
|---|---|
| V-19 | A2A 1.0 JSON serialization of `securitySchemes`/`securityRequirements` and `returnImmediately` in `a2a-sdk` 1.2.x and `a2a-java` 1.4.x (adjust Agent Cards and §1.3 if needed). |
| V-20 | Bifrost: `config.json` mounted read-only inside `/app/data` works; nested model names (`openrouter/<vendor>/<model>`, `ollama/<model>:<tag>`) route correctly; Ollama cost accounting in budgets. |
| V-21 | `mgconsole` exit code on failure (healthcheck semantics). |
| V-22 | `testcontainers` Neo4j module usable with the Memgraph image (else generic container). |
| V-23 | `eclipse-temurin:21-jre` vs. needed native libraries for ArDoCo/CoreNLP; image size. |
| Later | V-28…V-35 from the review decisions: Stage 3 §7. |
| Earlier | V-1 is largely answered by source reading (governance in OSS build without license check); runtime confirmation remains in the smoke test. V-9 answered (mgconsole present; `MEMGRAPH_USER`/`MEMGRAPH_PASSWORD`). V-11 answered (`nats:2.15.0-alpine` with wget; `-m 8222` required). V-8 partly answered (`--allow-load-csv=false` exists). |

## 8. Open questions

Answered 2026-10-06: Q-11 keep Memgraph (speed; ADR-0014) · Q-12 built-in user store · Q-13 A2A where it makes sense, plain HTTP where better (catalogs, plugin variant) · Q-14 GitHub Actions · Q-15 ArDoCo branch released/merged soon (pinned commit temporary). New questions: Stage 3 document.
