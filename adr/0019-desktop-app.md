---
status: proposed
date: 2026-11-07
---

# Desktop application (project-focused GUI)

## Context and Problem Statement

Users start SANDMAN with `make up`, work in a browser console and use the CLI for ingestion. A user who works on one project needs one lightweight entry point. It must start or connect to the system, hold the settings (keys, URLs), ingest folders and files, show progress and show results. Which technology and which integration style?

## Decision Drivers

* The focus is one project at a time. Switching the project is possible, an overview of all projects is not needed.
* The app must start and stop the local Docker Compose stack and warn the user on close.
* Small footprint, no new server component, reuse of existing APIs and the CLI.
* Existing contracts stay unchanged (ADR-0011, ADR-0013).

## Considered Options

* A: Tauri 2 shell with an own small UI that calls the REST API (`/v1`) through the Rust side.
* B: Tauri shell that only embeds the console and dashboard pages.
* C: Electron.
* D: Extend the server-rendered console only.

## Decision Outcome

Chosen option: "A", in `apps/desktop`. Tauri 2 (Rust backend, system webview), UI in TypeScript with Vite and without a UI framework, graph view with Cytoscape.

* The Rust side makes all HTTP calls with the project's bearer token (no CORS, token never in the webview). The token lives in the OS keychain.
* Core tabs (overview, ingest, workflows, findings, trace links, ask, graph) are native and use `/v1`. Pages without a native tab (administration, tokens, dashboard on `:8090`) open in the system browser.
* Local project: the app uses `make secrets`, `make up` and `make down` of the chosen checkout, and edits `deploy/secrets/gateway.env` and `deploy/.env`. Remote project: URL and token only, no life cycle control.
* Ingestion calls the `sandman` CLI (`uv run --project <checkout> sandman ingest`, or a `sandman` command that the user configures). The app does not copy the archive logic. Single files are staged in a temporary directory and sent with `--mode partial`.
* Progress is polling `GET /v1/projects/{id}/workflows` and `GET /v1/workflows/{id}`.
* Closing a local project whose stack runs asks: stop the containers, keep them running (the app shows the command `make down` in the checkout), or cancel. The app stops only the stack that it started or that the user confirms.
* Settings (keys, self-hosted model, CoreNLP mode) are saved as a default profile and pre-fill every new local project. Keys of the default profile are in the keychain.

Why not embed the console (B): the session cookie is `SameSite=Strict`, the dashboard sends `X-Frame-Options: DENY`, and the console CSP is `default-src 'self'`. An embedded page from another origin cannot log in. Why not Electron (C): larger runtime, no gain here. Why not D: it cannot start Docker or pick local files.

### Consequences

* Good, because one small binary, no server change, all authorization stays in the query-service.
* Good, because the stack life cycle and ingestion reuse `Makefile` and CLI.
* Bad, because a Rust toolchain and Node are needed to build the app.
* Bad, because the UI duplicates parts of the console. The console stays the full-featured interface.
* Bad, because the user still creates the personal token in the console once (initial admin password change required first). A login-and-create-token flow is planned.

### Confirmation

Unit tests of the Rust logic (project store, env file editing, command building, stack state parsing) and of the TypeScript view logic. A manual run against a running stack (`make up`) checks the tabs.

## More Information

* Related: ADR-0011, ADR-0013, ADR-0016. Task spec: [T-desktop-app](../docs/tasks/T-desktop-app.md).
