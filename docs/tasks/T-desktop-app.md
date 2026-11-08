# T-desktop-app Desktop application (initial version)

Status: initial version implemented (2026-11-07); manual acceptance run against a live stack still open. Decision: [ADR-0019](../../adr/0019-desktop-app.md).

## Goal

`apps/desktop`: a Tauri 2 application for one SANDMAN project at a time. It starts or connects to the system, stores settings, ingests folders and files, shows progress and results.

## Decisions on open points

| Point | Decision |
|---|---|
| UI stack | TypeScript, Vite, no UI framework, Cytoscape for the graph, plain CSS with light and dark theme (`prefers-color-scheme`) |
| HTTP | Rust `reqwest` with the bearer token; the webview never sees the token after it is saved |
| Secrets | OS keychain (`keyring` crate), service name `sandman-desktop`, one entry per project token and one per default-profile key |
| Project list | `projects.json` in the Tauri app config directory: `id`, `name`, `mode` (`local`/`remote`), `url`, `checkout` (local), `cliCommand` (optional), `lastUsed` |
| Local start | `make secrets` once, then `make up` in the checkout; stop with `make down`; state from `docker compose -f deploy/docker-compose.yml ps --format json`; ready when `GET <url>/readyz` is 200 |
| Local URL | `http://127.0.0.1:8080` |
| Keys | edit `deploy/secrets/gateway.env` (`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `SELFHOSTED_LLM_URL`, `SELFHOSTED_LLM_KEY`) and `SANDMAN_CORENLP_MODE` in `deploy/.env`; keep other lines and comments; default profile pre-fills new projects |
| Ingestion | `uv run --project <checkout> sandman ingest --project <id> --mode <m> <dir>` (or `cliCommand`); the CLI reads the token from `SANDMAN_TOKEN` and the URL from `SANDMAN_URL`; stdout and stderr stream to the UI; single files are copied into a temporary directory with relative paths below their common parent and sent with `--mode partial` |
| Progress | poll `GET /v1/projects/{id}/workflows` every 2 s; detail `GET /v1/workflows/{id}` with steps; trigger `POST /v1/projects/{id}/workflows` |
| Close | window close or project switch with a running local stack: dialog with three buttons: stop containers (`make down`), keep running (show the `make down` command and the checkout path, copyable), cancel |
| Graph | `POST /v1/query/cypher` with project-anchored statements (`MATCH (p:Project {id: $projectId})...`), Cytoscape view, click expands neighbours |
| Out of scope (v1) | administration, token management, dashboard `:8090` (buttons open the browser), password change, auto-update, signed installers, creating a token in the app |

## Tauri commands (Rust, `src-tauri/src/`)

| Command | Purpose |
|---|---|
| `list_projects`, `save_project`, `delete_project` | project store |
| `set_token(projectId, token)`, `has_token(projectId)` | keychain |
| `get_defaults`, `save_defaults` | default profile (keys in keychain, non-secret values in `defaults.json`) |
| `read_local_settings(checkout)`, `write_local_settings(checkout, settings)` | `gateway.env` and `deploy/.env` editing |
| `stack_state(projectId)` | `stopped` / `starting` / `running` / `partial` and ready flag |
| `stack_up(projectId)`, `stack_down(projectId)` | run `make` targets, stream lines as event `stack-log` |
| `api(projectId, method, path, query, body)` | authenticated request, returns status and JSON; errors carry the problem `detail` |
| `pick_folder`, `pick_files` | native dialogs (`tauri-plugin-dialog`) |
| `ingest(projectId, paths, mode, revision)` | run the CLI, stream event `ingest-log`, return exit code |
| `open_external(url)` | system browser (`tauri-plugin-opener`) |

The close dialog is implemented with the window close event: Rust prevents the close, asks the frontend, then exits.

## UI

* Start screen: list of projects (last used first), buttons **New project**, **Open**. The list is always shown; double click opens a project.
* New project wizard: name, id (slug, `^[a-z0-9][a-z0-9-]{1,62}$`), mode. Local: checkout folder (validated: `deploy/docker-compose.yml` exists), keys form pre-filled from defaults, checkbox "save as default". Remote: URL and token.
* Project view with header (project name, stack state, **Start**/**Stop**, **Switch project**) and tabs: **Overview** (service status, latest workflow, counts of findings by status), **Ingest** (pick folder or files, mode, revision, live log, workflow progress with steps), **Workflows**, **Findings** (list, filters, detail with evidence, triage with cause), **Trace links** (list, filter by status and approach, review), **Trace viewer** (columns with links and findings, link review; see [T-trace-viewer](T-trace-viewer.md)), **Ask**, **Graph**, **Settings** (keys, token, URL, CLI command, "open console" and "open dashboard" buttons). Ctrl/Cmd+1 to 9 select the tabs in this order.
* Token missing in a local project: the Overview shows steps: start the stack, open the console, change the admin password, create a token, paste it.
* Errors: show the problem `detail`; `401` leads to the token field; unreachable system shows state and a start button (local).

## Layout

```text
apps/desktop/
  package.json, vite.config.ts, tsconfig.json, index.html
  src/            main.ts, api.ts, views/*.ts, style.css
  src-tauri/      Cargo.toml, tauri.conf.json, capabilities/, src/{main,lib,store,env,stack,api,ingest}.rs
  README.md
```

`apps/desktop` is not part of the uv workspace, the Maven build or `libs/typescript`. Add Makefile targets `desktop-dev`, `desktop-build`, `desktop-test` (`npm test` and `cargo test`). CI: a path-filtered job that runs typecheck, `npm test` and `cargo test` (no bundle build).

## Acceptance

1. `cargo test` passes: project store round trip, env file editing keeps unrelated lines, `make` argument building, compose `ps` parsing, staged single-file layout.
2. `npm test` and `npm run typecheck` pass: view logic (slug validation, workflow progress mapping, problem message).
3. `npm run tauri build -- --no-bundle` compiles.
4. Manual: against `make up`, create a local project, paste a token, ingest `tests/e2e/fixtures/mini/s1`, see the workflow finish, see findings, graph and trace links, close with the stack running and see the three-way dialog.
