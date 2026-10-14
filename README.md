# sandman
Agent-based system for Software Architecture kNowledge Description and MANagement

SANDMAN ingests a project snapshot (requirements, documentation, architecture models, code), lets plugin agents extract knowledge into a graph (Memgraph), derive trace links and detect inconsistencies, and exposes the results through a query service with a web console and an API. Agents talk A2A over NATS; LLM calls go through a gateway.

- Plan: [docs/stage-3-implementation-plan.md](docs/stage-3-implementation-plan.md)
- Plugin authors: [docs/plugins.md](docs/plugins.md)
- Operators and project users: [docs/user/](docs/user/README.md)
- Component graph (generated with `make site`): <https://ardoco.de/sandman/>

## Status

The walking skeleton runs end to end: upload, extraction, trace link recovery, inconsistency detection, query. It includes the ArDoCo and LiSSA trace link approaches, the ArDoCo code extraction plugin, the system dashboard, and record/replay of LLM calls for reproducible runs. Natural language queries currently answer a fixed set of question patterns. The query strategies `graphrag`, `explore` and automatic selection are planned ([ADR-0018](adr/0018-query-strategies.md)). `make backup` is not implemented.

## Components

- `services/extraction-agent` (Python): information extraction agent
- `services/document-converter` (Python): PDF/DOCX to Markdown with Docling, called by the extraction agent
- `services/code-extractor-ardoco` (Java): ArDoCo code extraction plugin
- `services/tlr-agent`, `services/inconsistency-agent` (Java, Quarkus): traceability link recovery and inconsistency detection agents
- `services/orchestrator` (Python): rule-based orchestrator
- `services/query-service` (Python): API and console
- `services/dashboard` (Python): admin system overview
- `services/llm-replay` (Python): OpenAI-compatible record/replay proxy
- `libs/python/sandman-agent`, `libs/java/agent-lib` (+ `agent-lib-a2a`): agent libraries
- `tools/cli`: `sandman` CLI (`ingest`, `workflows`, `export`)
- `tests/plugin-stubs`, `tests/e2e`, `tests/conformance`: plugin stubs, end-to-end and conformance tests
- `deploy/`: Docker Compose stack, edge proxy, LLM gateway config, CoreNLP gateway and provider, secret generation

## Quickstart

```sh
make secrets       # generates deploy/secrets/*; put provider keys (OPENAI_API_KEY, OPENROUTER_API_KEY) into deploy/secrets/gateway.env
make up            # whole system (first image build takes several minutes)
```

Console: http://127.0.0.1:8080/console/login (user `admin`, initial password in `deploy/secrets/admin_bootstrap_password`, changed on first login). Create a project and a personal API token there, then:

```sh
export SANDMAN_TOKEN=...   # SANDMAN_URL defaults to http://127.0.0.1:8080
uv run sandman ingest --project demo --mode full tests/e2e/fixtures/mini/s1
uv run sandman workflows --project demo
uv run sandman export --project demo tracelinks
```

`make e2e` runs the walking skeleton with plugin stubs, dashboard and replay determinism tests against the stack in replay mode (first build about 6 min). It changes the bootstrap admin password to the one in `deploy/secrets/e2e_admin_password`.

Optional compose profiles: `stubs`, `dashboard` (system overview on :8090), `obs` (Grafana on :3001), `resources` (cAdvisor), `local-llm` (Ollama container for the `local-only` routes; `make models` pulls the models).

Self-hosted models (`chat-local`, `embed-local`, `local-only` policy) are served either by the local Ollama container (default: `SELFHOSTED_LLM_URL=http://ollama:11434`, profile `local-llm`, `make models`) or by a remote Open WebUI/Ollama (`SELFHOSTED_LLM_URL=https://<host>/ollama`, `SELFHOSTED_LLM_KEY=<Open WebUI API key>`), both set in `deploy/secrets/gateway.env` (ADR-0004). `make up`/`infra`/`e2e` run `deploy/check-env.sh` and refuse any other non-`https://` URL or an empty key.

## Development

Requirements: [uv](https://docs.astral.sh/uv/) (Python 3.13), JDK 21+ with Maven 3.9+, Docker with Compose.
Python parts are managed with uv only, Java parts are built with Maven only. Java naming is fixed: Maven groupId `io.github.ardoco` (artifactIds prefixed `sandman-`, e.g. `sandman-agent-lib`), Java packages start with `edu.kit.kastel.mcse.ardoco` (SANDMAN code under `edu.kit.kastel.mcse.ardoco.sandman`).

```sh
uv sync            # Python workspace (libs/python, services, tools/cli, tests/plugin-stubs)
make test          # uv run pytest + mvn -B verify
```

Integration tests need Docker running. On Docker Desktop, Python Testcontainers needs `DOCKER_HOST=unix://$HOME/.docker/run/docker.sock`.

| Target | What it does |
|---|---|
| `make secrets` | Generates `deploy/secrets/*` (never overwrites); add provider keys to `deploy/secrets/gateway.env` |
| `make infra` | Starts Memgraph, NATS and the LLM gateway with dev ports on localhost |
| `make up` / `make down` / `make logs` | Whole system via Docker Compose |
| `make test` | `make test-py` (`uv run pytest`) and `make test-java` (`mvn -B verify`, incl. Spotless and the enforcer) |
| `make lint` | Framework guard, `ruff check`, `ruff format --check`, `mypy libs/python` |
| `make guard` | Fails if Python code under `libs/` or `services/` imports an agent framework |
| `make contracts` | Validates `contracts/` (schemas, conformance vectors, Agent Cards, OpenAPI, KG schema) |
| `make images` | Builds all images of the `stubs` profile |
| `make e2e` | Walking skeleton, dashboard and replay determinism tests against the stack with plugin stubs |
| `make backup` | Not implemented, exits 1 |

Design: [docs/](docs/) (stage 1–3, task specs), decisions: [adr/](adr/README.md), review: [docs/stage-4-review.md](docs/stage-4-review.md).
