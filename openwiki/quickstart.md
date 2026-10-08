---
type: quickstart
title: SANDMAN Quickstart
description: Task-routing entry point for the SANDMAN repository — the fixed toolchain rules (uv for Python 3.13, Maven for JDK 21, Docker Compose for the stack), the core make targets, and a map from common developer tasks to the wiki page that owns each one.
tags: [quickstart, toolchain, uv, maven, jdk-21, make-targets, docker-compose, task-routing]
verified:
  - by: openwiki/0.5.2
    at: 2026-10-07T15:57:21.237Z
sources:
  - id: openwiki-source-164e2da859b5277df81c7d94
    resource: repo://.github/workflows/ci.yml
  - id: openwiki-source-b94b42f21da46b11f148864a
    resource: repo://config/extraction/plugins.yaml
  - id: openwiki-source-39b446fb18e7ddf4812ef534
    resource: repo://deploy/docker-compose.yml
  - id: openwiki-source-7fdd8730e698e2a5f13be3e8
    resource: repo://deploy/gen-secrets.sh
  - id: openwiki-source-ae8fd9a1faaee324256a83ec
    resource: repo://deploy/proxy/Caddyfile
  - id: openwiki-source-1abeefc270f483f845d5e74d
    resource: repo://docs/stage-2-contracts-repository.md
  - id: openwiki-source-eb87d6daa38035a49139b75c
    resource: repo://libs/java/agent-lib/pom.xml
  - id: openwiki-source-67be01257242aada219fe3ae
    resource: repo://libs/java/agent-lib/src/main/java/edu/kit/kastel/mcse/ardoco/sandman/agent/Settings.java
  - id: openwiki-source-54e2d87e97b860e430c593bf
    resource: repo://libs/python/sandman-agent/tests/test_integration.py
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-2355f81d7cf522f8dbdaabd4
    resource: repo://pom.xml
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-06bab61b138affbc454636a9
    resource: repo://scripts/validate_contracts.py
  - id: openwiki-source-978bfb5a18e1159117bc728b
    resource: repo://tests/e2e/test_walking_skeleton.py
  - id: openwiki-source-f4c400d485658ca533348dc7
    resource: repo://tools/cli/pyproject.toml
  - id: openwiki-source-e4ba50b6d12e6f0659bbaa6e
    resource: repo://tools/cli/src/sandman_cli/__main__.py
generated: { by: "openwiki/0.5.2", at: "2026-10-07T15:57:21.237Z" }
---

# SANDMAN Quickstart

SANDMAN ("Agent-based system for Software Architecture kNowledge Description and MANagement") ingests snapshots of a software project, extracts them into a Memgraph knowledge graph, recovers trace links between artifact types, detects potential inconsistencies, and gives users a console to triage findings and query the graph. This page is the fast way in: the toolchain rules that are fixed, the make targets that drive everything, and a routing table that sends each task to the page that owns it. The architectural big picture lives in [System Overview](/openwiki/architecture/system-overview.md); design rationale lives in `adr/` (MADR-format ADRs) and the stage documents in `docs/`.

## Fixed toolchain rules

Two build rules are non-negotiable and enforced by the repository itself:

- **Python is managed with uv only.** Python 3.13 (`requires-python = ">=3.13,<3.14"`), one uv workspace at the repository root, one `uv.lock`. Never pip/poetry/conda; CI runs `uv sync --locked`.
- **Java is built with Maven only.** JDK 21 (`maven.compiler.release=21`; the enforcer requires Maven ≥ 3.9 and Java ≥ 21), one aggregator POM at the root.

Naming is fixed too:

| Concern | Rule |
|---|---|
| Maven coordinates | groupId `io.github.ardoco`; artifactIds prefixed `sandman-` (`sandman-parent`, `sandman-agent-lib`, `sandman-agent-lib-a2a`, `sandman-tlr-agent`, `sandman-inconsistency-agent`) |
| Java packages | all SANDMAN code under `edu.kit.kastel.mcse.ardoco.sandman` |
| Python packages | distributions `sandman-*`, import packages `sandman_*` (`sandman_agent`, `sandman_extraction`, `sandman_cli`, …) |

Agent frameworks are banned (NFR-3): `make guard` fails on Python imports of `langchain`, `langgraph`, `deepagents`, `llama_index`, `crewai` or `autogen` anywhere under `libs/` and `services/`, and the Maven enforcer bans `dev.langchain4j`, `org.springframework.ai` and `org.bsc.langgraph4j` dependencies (LangChain4j is tolerated only as a transitive dependency inside the reused ArDoCo/LiSSA adapters).

## First commands

```sh
uv sync            # Python workspace: libs/python, the Python services, tools/cli, tests/plugin-stubs
make test          # uv run pytest + mvn -B verify (both language suites)
```

Docker with Compose is required to run the stack; the container-backed integration tests skip themselves automatically when no Docker daemon is reachable.

## Core make targets

| Target | What it does |
|---|---|
| `make secrets` | `deploy/gen-secrets.sh` → `deploy/secrets/*` (never overwrites existing files); add real provider keys to `deploy/secrets/gateway.env` |
| `make infra` | Memgraph, NATS and the LLM gateway only, with the dev overlay publishing Bolt 7687 and NATS 4222/8222 on localhost |
| `make up` / `make down` / `make logs` | Whole default-profile stack via `deploy/docker-compose.yml` |
| `make images` | Builds all images including `plugin-stubs` (`--profile stubs`) |
| `make test` | `make test-py` (`uv run pytest`) + `make test-java` (`mvn -B verify`, incl. Spotless and the enforcer) |
| `make lint` | `make guard` + `uv run ruff check .` + `uv run ruff format --check .` + `uv run mypy libs/python` |
| `make guard` | Fails if Python under `libs/` or `services/` imports an agent framework |
| `make contracts` | `uv run python scripts/validate_contracts.py` over `contracts/` (schemas, conformance vectors, Agent Cards, OpenAPI, KG schema) |
| `make e2e` | `secrets` → `--profile stubs up -d --build --wait --wait-timeout 300` → `SANDMAN_E2E=1 uv run pytest tests/e2e -q` |
| `make backup` | Placeholder: exits 1 with `not implemented (T5.3)` |

Three gotchas worth knowing before you use them:

- `tests/e2e` is **not** in the default pytest `testpaths`, so `make test` never runs the walking skeleton. Only `make e2e` (or a manually started stack plus `SANDMAN_E2E=1`) does — and it needs no LLM provider key. (The `README.md` target table still lists `make e2e` as a placeholder; the Makefile implements it.)
- `make up` returns as soon as containers are created — readiness arrives later through the health-gated `depends_on` chain — and it starts only the default profile. `stubs`, `obs`, `local-llm`, `dashboard` and `resources` are opt-in via `--profile`.
- `make e2e` is the only target that blocks on health (`--wait`); it also creates the extra `e2e_admin_password` secret on first run.

## Running the stack

```sh
make secrets && make up
```

Then work against `http://127.0.0.1:8080` — the Caddy edge proxy is the only published web entry point (console and user/agent API, reverse-proxied to the query-service); the Bifrost gateway admin UI sits on `http://127.0.0.1:8081`. Dashboard (8090) and Grafana (3001) are profile-gated. Networks, health gates, memory limits and the secrets flow are covered in [Deployment and Compose Stack](/openwiki/operations/deployment.md); per-service environment variables in [Configuration](/openwiki/operations/configuration.md).

## Where things live

| Path | Contents |
|---|---|
| `contracts/` | The machine-readable source of truth: JSON Schemas, A2A Agent Cards, OpenAPI per service, KG schema, conformance vectors |
| `libs/python/sandman-agent/`, `libs/java/agent-lib{,-a2a}/` | The two shared minimal agent libraries |
| `services/` | Python: `orchestrator`, `extraction-agent`, `query-service`, `llm-replay` · Java (Quarkus): `tlr-agent`, `inconsistency-agent` |
| `tools/cli/` | The `sandman` CLI (ingest, workflows, export) |
| `tests/` | `e2e/` walking skeleton, `plugin-stubs/` reference plugin, `conformance/` vectors runner |
| `config/` | Runtime YAML (orchestrator rules, TLR approaches, extraction/inconsistency plugins, query intents), mounted read-only at `/config` by the services that consume it |
| `deploy/` | `docker-compose.yml`, dev overlay, `gen-secrets.sh`, gateway and proxy config |
| `adr/`, `docs/` | MADR decision records · stage documents and task specs |

## Task routing map

| I want to… | Read |
|---|---|
| Get the big picture (components, languages, transports, cross-cutting rules) | [System Overview](/openwiki/architecture/system-overview.md) |
| Run, stop or debug the stack (profiles, networks, secrets, health gates) | [Deployment and Compose Stack](/openwiki/operations/deployment.md) |
| Set or change a service's configuration / environment variables | [Configuration and Environment Variables](/openwiki/operations/configuration.md) |
| Check service status, logs or tracing | [Status, Logging and Telemetry](/openwiki/operations/observability.md) |
| Run or write tests, fix lint, understand the CI pipeline | [Testing and CI](/openwiki/testing/testing-and-ci.md) |
| Change a service | [Orchestrator](/openwiki/services/orchestrator.md) · [Extraction Agent](/openwiki/services/extraction-agent.md) · [TLR Agent](/openwiki/services/tlr-agent.md) · [Inconsistency Agent](/openwiki/services/inconsistency-agent.md) · [Query Service](/openwiki/services/query-service.md) · [LLM Replay](/openwiki/services/llm-replay.md) |
| Change schemas, Agent Cards, OpenAPI documents or the KG schema | [Contract-First Interfaces](/openwiki/architecture/contracts.md) |
| Change the shared agent libraries (or add a new language kit) | [Agent Libraries (Python and Java)](/openwiki/architecture/agent-libraries.md) |
| Change the knowledge-graph model or persistence behavior | [Knowledge Graph Schema and Persistence](/openwiki/architecture/knowledge-graph.md) |
| Understand pipeline behavior (why did this workflow run, stall or dead-letter?) | [End-to-End Analysis Pipeline](/openwiki/workflows/analysis-pipeline.md) |
| Add a plugin (extractor or detector) | [Plugin System (Extractors and Detectors)](/openwiki/concepts/plugins.md) |
| Work on A2A skills or the skill-invocation convention | [A2A Skills and Agent Cards](/openwiki/concepts/a2a-skills.md) |
| Add or change an event type | [NATS JetStream Event Bus](/openwiki/concepts/event-bus.md) |
| Deal with authentication, tokens, roles or trusted forwarding | [Authentication, Roles and Trusted Forwarding](/openwiki/concepts/auth-and-roles.md) |
| Understand inconsistency states, triage or re-detection | [Inconsistency Lifecycle](/openwiki/concepts/inconsistency-lifecycle.md) |
| Query the graph in NL/Cypher or change the read guard | [Cypher Guard and NL Querying](/openwiki/concepts/cypher-guard.md) |
| Ingest a project or call SANDMAN from CI | [SANDMAN CLI](/openwiki/integrations/cli.md) |
| Configure LLM providers, virtual keys, budgets or local models | [LLM Gateway (Bifrost)](/openwiki/integrations/llm-gateway.md) |

That table is the whole wiki in one view: architecture (`/openwiki/architecture/`), cross-cutting concepts (`/openwiki/concepts/`), services (`/openwiki/services/`), operations (`/openwiki/operations/`), integrations (`/openwiki/integrations/`), testing and the end-to-end workflow.
