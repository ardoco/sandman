# sandman
Agent-based system for Software Architecture kNowledge Description and MANagement

SANDMAN ingests a project snapshot (requirements, documentation, architecture models, code), lets plugin agents extract knowledge into a graph (Traverse), derive trace links and detect inconsistencies, and exposes the results through a query service with a web console and an API. Agents talk A2A over NATS; LLM calls go through a gateway.

- Plugin authors: [docs/plugins.md](docs/plugins.md)
- Operators and project users: [docs/user/](docs/user/README.md)
- Component graph (generated with `make site`): <https://ardoco.de/sandman/>

## Status

The walking skeleton runs end to end: upload, extraction, trace link recovery, inconsistency detection, query. It includes the trace link approaches ArDoCo, LiSSA and TransArC (default for document to code, with architecture models derived from documentation when a project has none), the ArDoCo code extraction plugin and the ArDoCo inconsistency detector. Files without a matching kind rule are classified automatically, and a changed rule re-classifies existing artifacts. Triagers review trace links and give a cause for each triage decision. The console has a graph explorer. The dashboard shows the system for admins. Retention prune, backup and restore, and the GitHub Action for ingestion exist. LLM calls can be recorded and replayed for reproducible runs. Natural language queries answer fixed question patterns first and use the LLM strategy `cypher` otherwise. The strategies `graphrag` and `explore` exist, but `config/query/strategies.yaml` disables them ([ADR-0018](adr/0018-query-strategies.md)). Planned: the change impact report (T6.3) and the LLM detector `artemis-id`, which is disabled by default.

## Components

- `services/extraction-agent` (Python): information extraction agent
- `services/document-converter` (Python): PDF/DOCX to Markdown with Docling, called by the extraction agent
- `services/code-extractor-ardoco` (Java): ArDoCo code extraction plugin
- `services/tlr-agent`, `services/inconsistency-agent` (Java, Quarkus): traceability link recovery and inconsistency detection agents
- `services/orchestrator` (Python): rule-based orchestrator
- `services/query-service` (Python): API and console (projects, findings with triage causes, trace links with review, trace view queries, ask, graph explorer, upload, workflows, tokens, administration)
- `services/dashboard` (Python): admin system overview (services, projects, graph page, trace viewer page, classification page, gateway, workflows, events, history, resources)
- `services/llm-replay` (Python): OpenAI-compatible record/replay proxy
- `libs/python/sandman-agent`, `libs/java/agent-lib` (+ `agent-lib-a2a`): agent libraries; `sandman_agent.graphview` is the graph conversion shared by console and dashboard
- `actions/sandman-ingest`: GitHub Action that ingests a repository, waits for the workflow and writes the change impact to the job summary
- `tools/cli`: `sandman` CLI (`ingest`, `workflows`, `export`, `impact`, `eval`)
- `tests/plugin-stubs`, `tests/e2e`, `tests/conformance`: plugin stubs, end-to-end and conformance tests
- `apps/desktop` (Tauri 2, TypeScript): desktop app for one project at a time; starts or connects to the system, ingests, shows results (see [Desktop app](#desktop-app))
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

`make e2e` runs the walking skeleton with plugin stubs, the link review and triage cause test, dashboard and replay determinism tests against the stack in replay mode (first build about 6 min). It changes the bootstrap admin password to the one in `deploy/secrets/e2e_admin_password`.

Optional compose profiles: `stubs`, `dashboard` (system overview on :8090), `obs` (Grafana on :3001), `resources` (cAdvisor), `local-llm` (Ollama container for the `local-only` routes; `make models` pulls the models).

Self-hosted models (`chat-local`, `embed-local`, `local-only` policy) are served either by the local Ollama container (default: `SELFHOSTED_LLM_URL=http://ollama:11434`, profile `local-llm`, `make models`) or by a remote Open WebUI/Ollama (`SELFHOSTED_LLM_URL=https://<host>/ollama`, `SELFHOSTED_LLM_KEY=<Open WebUI API key>`), both set in `deploy/secrets/gateway.env` (ADR-0004). `make up`/`infra`/`e2e` run `deploy/check-env.sh` and refuse any other non-`https://` URL or an empty key.

## Desktop app

The desktop app in `apps/desktop` needs Node 22+, Rust (stable) and the Tauri system libraries. Details: [apps/desktop/README.md](apps/desktop/README.md).

```sh
make desktop-dev     # development mode
make desktop-build   # bundle in apps/desktop/src-tauri/target/release/bundle/
```

Start the built app on macOS with `open apps/desktop/src-tauri/target/release/bundle/macos/SANDMAN.app`. Start it on Linux or Windows with `apps/desktop/src-tauri/target/release/sandman-desktop` (`.exe` on Windows).

## Development

Requirements: [uv](https://docs.astral.sh/uv/) (Python 3.13), JDK 21+ with Maven 3.9+, Docker with Compose.
Python parts are managed with uv only, Java parts are built with Maven only. Java naming is fixed: Maven groupId `io.github.ardoco` (artifactIds prefixed `sandman-`, e.g. `sandman-agent-lib`), Java packages start with `edu.kit.kastel.mcse.ardoco` (SANDMAN code under `edu.kit.kastel.mcse.ardoco.sandman`).

```sh
uv sync            # Python workspace (libs/python, services, tools/cli, tests/plugin-stubs)
make test          # uv run pytest + mvn -B verify
```

Mutation testing for Java uses PIT (pitest-maven, profile `mutation`). The profile is not part of `mvn verify`.
Run PIT for one module with these steps:

```sh
mvn -B -Pmutation -pl libs/java/agent-lib test-compile org.pitest:pitest-maven:mutationCoverage \
  -DtargetClasses='edu.kit.kastel.mcse.ardoco.sandman.agent.Ids' -DtargetTests='edu.kit.kastel.mcse.ardoco.sandman.*'
```

Open `target/pit-reports/index.html` in the module to see the surviving mutants. The profile excludes `*IntegrationTest` classes because they need Docker.

Run mutation tests for the Rust agent kit with cargo-mutants. Install it once with `cargo install cargo-mutants --locked`. Run one module with this command:

```sh
cd libs/rust && cargo mutants --in-place --file sandman-agent/src/ids.rs
```

Use `--in-place` because the tests read `contracts/` relative to the crate. Do not run other builds in `libs/rust` during the run. Read `libs/rust/mutants.out/missed.txt` for the surviving mutants. The settings are in `libs/rust/.cargo/mutants.toml`. Docker is not needed.

Integration tests need Docker running. On Docker Desktop, Python Testcontainers needs `DOCKER_HOST=unix://$HOME/.docker/run/docker.sock`.

| Target | What it does |
|---|---|
| `make secrets` | Generates `deploy/secrets/*` (never overwrites); add provider keys to `deploy/secrets/gateway.env` |
| `make infra` | Starts Traverse, NATS and the LLM gateway with dev ports on localhost |
| `make up` / `make down` / `make logs` | Whole system via Docker Compose |
| `make test` | `make test-py` (`uv run pytest`) and `make test-java` (`mvn -B verify`, incl. Spotless and the enforcer) |
| `make lint` | Framework guard, `ruff check`, `ruff format --check`, `mypy libs/python` |
| `make mutate PKG=<dir>` | Runs mutation tests (mutmut) for the modules in `[tool.mutmut]` of `<dir>/pyproject.toml`, for example `services/query-service`. Show results with `cd <dir> && uv run mutmut results`. Docker is not needed |
| `make guard` | Fails if Python code under `libs/` or `services/` imports an agent framework |
| `make contracts` | Validates `contracts/` (schemas, conformance vectors, Agent Cards, OpenAPI, KG schema) |
| `make images` | Builds all images of the `stubs` profile |
| `make e2e` | Walking skeleton, dashboard and replay determinism tests against the stack with plugin stubs |
| `make backup`, `make restore ARCHIVE=<file>` | Archive and restore the running stack ([operations guide](docs/user/operations.md)) |

Design: [docs/](docs/), decisions: [adr/](adr/README.md).
