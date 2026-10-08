# Operations

All commands run in the repository root. The stack is defined in `deploy/docker-compose.yml` (project name `sandman`); the `make` targets are thin wrappers around `docker compose -f deploy/docker-compose.yml`.

## Start, stop, status

| Command | Effect |
|---|---|
| `make secrets` | create `deploy/secrets/*`, never overwrites ([getting-started.md](getting-started.md)) |
| `make up` | run `deploy/check-env.sh`, then build and start the whole system (default profile) |
| `make down` | stop and remove the containers; named volumes (data) are kept |
| `make logs` | follow the logs of all services |
| `make images` | build the images of the `stubs` profile |
| `docker compose -f deploy/docker-compose.yml ps` | state and health of each container |
| `curl http://127.0.0.1:8080/readyz` | readiness of the query service: Memgraph and user store |

`docker compose -f deploy/docker-compose.yml down -v` additionally deletes all data (see below). It cannot be undone.

## Services and ports

Everything is published on `127.0.0.1` only.

| Address | Service | Audience |
|---|---|---|
| `127.0.0.1:8080` | edge proxy to the query service: console, REST API | users |
| `127.0.0.1:8081` | LLM gateway admin UI and request logs (log in with the password in `deploy/secrets/gateway_admin_password`) | operators |
| `127.0.0.1:8090` | system overview (dashboard), see [Dashboard](#dashboard); profile `dashboard` | admins |
| `127.0.0.1:3001` | Grafana, profile `obs` | operators |

Memgraph and NATS have no host ports. `make infra` starts only Memgraph, NATS and the gateway with development ports (Bolt `7687`, NATS `4222`, NATS monitoring `8222`) for developers.

The containers share an internal network without internet access. The `document-converter` (Docling) is reachable only inside it, mounts the artifact store read-only and accepts conversion requests only from the extraction agent; if it is down, PDF and DOCX artifacts fall back to file-level elements (`partial`). Only the LLM gateway has internet access (to the providers you configured keys for, or the remote Open WebUI); with profile `local-llm` the Ollama container also has egress for model pulls, and talks to nobody but the gateway.

## Compose profiles

| Profile | Adds | Start |
|---|---|---|
| (default) | Memgraph, NATS, LLM gateway, replay proxy, extraction, document converter (PDF/DOCX), TLR, inconsistency agent, orchestrator, query service, edge proxy | `make up` |
| `stubs` | `plugin-stubs`: stub code extractor and stub detector used by the shipped config and the end-to-end test | `docker compose -f deploy/docker-compose.yml --profile stubs up -d --build` |
| `obs` | Grafana/OpenTelemetry backend on `:3001`; services export telemetry when `OTEL_SDK_DISABLED=false` and `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-lgtm:4318` are set in the shell that runs compose | `... --profile obs up -d` |
| `resources` | cAdvisor container metrics for the dashboard. **Security: grants root-equivalent access to the host through the Docker socket (read-only socket proxy plus host mounts); off by default, not for production.** | `... --profile resources up -d` |
| `local-llm` | Ollama container (`ollama/ollama:0.35.1`, 4 GB, CPU-only, volume `ollama-models`) for `chat-local`/`embed-local`; needs `SELFHOSTED_LLM_URL=http://ollama:11434` (the default of `make secrets`) | `make models` (starts it and pulls the `ollama/*` models of `config/llm/models.yaml`) |
| `dashboard` | system overview for admins | `... --profile dashboard up -d --build` |

Combine profiles by repeating `--profile`. The `stubs` profile belongs to demos and tests, not to a production setup; the shipped plugin entries in `config/` point to it (see below).

## Dashboard

With profile `dashboard`, `http://127.0.0.1:8090` shows (log in as `admin` with `deploy/secrets/dashboard_admin_password`): services and infrastructure health, projects (current-state statistics from the knowledge graph), the knowledge graph, the LLM gateway, workflows (with cancel), dead events (with replay), the event stream (last 500 events, read-only), history charts (health, activity and LLM usage, sampled every 30 s and kept for 14 days; `SANDMAN_SAMPLE_INTERVAL`, `SANDMAN_HISTORY_DAYS`), record/replay status (experiments) and resource usage. Cancel and replay ask for confirmation and are written to the dashboard log as `event=admin_action`. Alerts (service down for more than 30 s, failed or blocked workflows of the last 24 h, dead events, gateway budget at 80 % or more, Memgraph memory at 80 % of its limit) appear as a banner on every page and as `event=alert` log lines; they are kept in memory and cleared when the condition ends.

The Resources page needs profile `resources` (cAdvisor behind a read-only Docker socket proxy on its own internal network, shared only with the dashboard). Enabling it gives these containers root-equivalent visibility of the host; use it for development and diagnosis only, never in production. Without it the page says so and everything else keeps working. On Docker Desktop add `-f deploy/docker-compose.resources-desktop.yml` (mounts the containerd socket, same warning). See `docs/notes/cadvisor.md` for platform notes.

## Configuration

`config/` is mounted read-only into the services and read when a service starts. After editing, restart the affected service, for example:

```sh
docker compose -f deploy/docker-compose.yml restart extraction-agent
```

| File | Controls |
|---|---|
| `config/extraction/plugins.yaml` | extraction plugins, explicit reference patterns, `builtin.enabled` (`false` falls back to file-level extraction only) |
| `config/extraction/enrichment.yaml` | LLM enrichment (summaries, concepts): `enabled`, enriched element kinds, element limit per run, concurrency, `maxSummaryChars`, `maxConceptsPerElement` |
| `config/llm/models.yaml` | logical LLM routes (`chat-default`, `chat-alt`, `embed-default` need provider keys; `chat-local`, `embed-local` use the self-hosted models) |
| `config/inconsistency/plugins.yaml` | enabled detectors and detector plugins |
| `config/tlr/approaches.yaml` | enabled trace link approaches and their parameters |
| `config/orchestrator/rules.yaml` | debounce, concurrency, timeouts, retries, kind pairs for link recovery |
| `config/query/intents.yaml` | question patterns of **Ask** |
| `config/query/plugins.yaml` | projects each plugin may read |

Plugin authors: [plugins.md](../plugins.md).

## Data, logs, backup

| Data | Location |
|---|---|
| graph | volume `memgraph-data` (snapshot every 300 s) |
| users, sessions, tokens | volume `query-data` (`users.db`) |
| workflow state | volume `orchestrator-data` |
| uploaded file contents | volume `artifact-store` |
| events | volume `nats-data` |
| gateway state | volume `gateway-data` |
| logs, LLM records | `data/logs`, `data/llm-records` in the repository directory |

Logs are JSON lines on stdout: `make logs`, or one service with `docker compose -f deploy/docker-compose.yml logs -f extraction-agent`. Setting `SANDMAN_LOG_FILE=/logs/sandman.jsonl` in the shell that runs compose also writes them to `data/logs/`.

Backup is not implemented: `make backup` prints `not implemented (T5.3)` and exits with 1. There is no supported backup or restore procedure yet.

## Troubleshooting

| Symptom | Cause and remedy |
|---|---|
| `check-env: ...` and `make` stops | `deploy/secrets/gateway.env` is missing (run `make secrets`), `SELFHOSTED_LLM_URL` is neither `http://ollama:11434` nor `https://...`, or an `https://` URL comes with an empty `SELFHOSTED_LLM_KEY` (for Open WebUI: the API key) |
| `Cannot connect to the Docker daemon` | start Docker (Docker Desktop on macOS). `DOCKER_HOST=unix://$HOME/.docker/run/docker.sock` is only needed for the Python integration tests (`make test`), not for running the stack |
| port already in use | change the published ports of `edge-proxy` and `llm-gateway` in `deploy/docker-compose.yml`. To run a second stack, also give it its own project name: `docker compose -p other -f ...` |
| containers killed or Memgraph restarts | the memory limits assume a 32 GB host (Memgraph 10 GB, TLR and inconsistency agents 5 GB each); give Docker enough memory |
| `readyz` returns 503 | the `checks` object names the failing part (`memgraph`, `userstore`); look at that service's logs |
| `503 <service> unavailable` from the API or "unavailable" as last workflow in the console | the extraction agent, inconsistency agent or orchestrator is down or still starting; check `docker compose ps` |
| login fails with 429 | five wrong passwords per user name or address within 15 minutes; wait |
| `403 password change required` with a token | the token's owner has not yet changed the initial password in the console |
| `404 not found` for a project that exists | you have no role in it; ask an admin ([projects-and-access.md](projects-and-access.md)) |
| `sandman ingest` returns `403` | role below `maintainer` |
| workflow `SUCCEEDED` but code and test files have only file-level elements, runs are `partial` or `failed` | the plugin stubs are not running; start the `stubs` profile or adjust `config/extraction/plugins.yaml` and `config/inconsistency/plugins.yaml` |
| a changed file did not change its kind | kinds are fixed when an artifact is first ingested ([ingesting-artifacts.md](ingesting-artifacts.md)) |
| a failed workflow does not restart on re-ingestion | unchanged snapshots start nothing; trigger a workflow manually ([workflows.md](workflows.md)) |
| nothing explains a partial run | inspect `Run` nodes with the query in [ingesting-artifacts.md](ingesting-artifacts.md) and the extraction agent log |
| lost the only admin password | another admin can reset it on the **Administration** page. There is no supported reset otherwise: the initial account is created only while the user database is empty |
