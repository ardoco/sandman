# Desktop app: export trace links and findings as CSV, also in benchmark formats

Milestone: desktop app (follow-up of `T-desktop-settings.md`) · Effort M · Depends on: T-desktop-settings

Status: planned (2026-11-13)

## Context

The desktop app shows trace links and findings. It cannot export them. A user who wants a CSV file for a gold standard comparison must use the CLI (`sandman export`) or the REST API.

Two kinds of export exist today:

| Kind | Where | Formats |
|------|-------|---------|
| REST routes | `GET /v1/projects/{id}/export/tracelinks` and `.../export/inconsistencies` (`query-service`) | `csv`, `json`; filters `approach`, `minConfidence`, `includeRejected` (trace links) |
| CLI | `sandman export` (`tools/cli`, `evaluation/formats.py`) | also the benchmark formats `ardoco-sad-sam`, `ardoco-sad-code`, `ardoco-sam-code`, `lissa`, with `--approach` and `--code-root` |

The benchmark formats exist only in the CLI. The CLI reads the links with a Cypher query and converts them on the client side. The REST routes cannot produce them.

## Goal

The user exports trace links and findings from the app to a file. The user chooses the format and the approach, and sees where the file is saved. The benchmark formats work for gold standard comparison, as in `docs/user/evaluation.md`.

## Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Where | A new project tab "Export". It lists two blocks: trace links and findings. |
| 2 | Generic formats | `csv` and `json` through the REST routes, for local and remote projects. The Rust side downloads the body and writes it to the chosen file. |
| 3 | Benchmark formats | `ardoco-sad-sam`, `ardoco-sad-code`, `ardoco-sam-code`, `lissa` through the CLI (`sandman export --format ...`). The app does not re-implement the conversion in Rust. One implementation stays the source of truth. |
| 4 | CLI source | The command of the project (`cliCommand`, or the default `uv run --project <checkout> sandman`), as the ingest and classify views use it. The token goes to the child process in `SANDMAN_TOKEN`, never in the arguments. |
| 5 | Missing CLI | A project without a CLI command and without a checkout (typically a remote project) shows the benchmark formats as disabled, with the hint "Set a CLI command in the project settings". The generic formats stay available. |
| 6 | Trace link options | Approach (list from the project: all, or one approach), `minConfidence` (generic only), `includeRejected` (generic only), `code-root` (only for `ardoco-sad-code`). |
| 7 | Findings | `csv` and `json`. No filter. |
| 8 | Target file | A save dialog (`tauri-plugin-dialog`, already installed) with a default name `<project>-<what>[-<approach>].<csv or json>`. The user can cancel. |
| 9 | Overwrite | The save dialog asks before it overwrites. The app never writes outside the chosen path. |
| 10 | Result | A toast "Saved N rows to <path>" with a button to reveal the file. The row count comes from the lines of the file minus the header. |
| 11 | Preview | Show the first 10 rows below the form before the user saves. Optional per step 3. |
| 12 | Errors | A 401 or 403 shows `tokenMessage`. A CLI failure shows the last lines of its stderr. A token or password never appears in an error. |
| 13 | i18n | New texts go through `t()` with English and German entries, as in `T-desktop-settings.md`. |
| 14 | Gold standard comparison | Out of scope for the app. The docs show the CLI evaluation command for the exported file. |

## Steps

Each step ends with its check. Keep the handoff note `.work/T-desktop-export.md` up to date. Commit each step.

1. Rust command `export_generic(projectId, what, format, approach?, minConfidence?, includeRejected?, path)`: load the project and token, call the REST route, write the body to `path`, return `{ rows, path }`. Validate the format and the numbers. Reuse the HTTP client and the error mapping of `api.rs`.
   Check: tests for the query string builder (parameters encoded, defaults omitted), the row counter, and the file write into a temp directory.
2. Rust command `export_benchmark(projectId, format, approach?, codeRoot?, path)`: run the project CLI with `export tracelinks --format ... --project ...`, set `SANDMAN_TOKEN` in the environment, write stdout to `path`. Reuse the CLI runner of `ingest.rs` or `classify.rs`.
   Check: a pure test for the argument list (no token in the arguments, `--code-root` only for `ardoco-sad-code`, `--approach` only if set) and for the environment of the child.
3. View `src/views/export.ts` and the tab "Export". Fields: what, format, approach, options per decision 6. Button "Export" opens the save dialog, calls the command, shows the toast. Add the helpers (`exportFileName`, `availableFormats(project)`, `exportSummary`) to `logic.ts`.
   Check: vitest for the three helpers (disabled benchmark formats without CLI, file names, summary text) and for the key sets of `en` and `de`. `npm run typecheck`, `npm run build`.
4. Docs: `docs/user/desktop-app.md` (section "Export"), `docs/user/evaluation.md` (how to compare the exported file), `apps/desktop/README.md`. Controlled language rules apply.
   Check: docs link check of CI passes.
5. Full verification: `cargo test --locked`, `npm test`, `npm run typecheck`, `npm run build`, `./guard.sh`, `make lint`, `make contracts`. Run the app against a local stack if possible and export each format once. If not possible, say so.
6. Example repository: update the README of `sandman-example` (`/Users/keim/Projects/repos/ArDoCo/sandman-example`) with the "Export" tab, after steps 1 to 5 are done. Mention both formats, the benchmark format for the files in `goldstandards/`, and the CLI hint. Commit and push to its `origin main`.
   Check: each path and name in the README exists in the repository and in the app.

## Acceptance criteria

1. The tab "Export" saves trace links as `csv` and `json` for a local and a remote project.
2. The tab saves findings as `csv` and `json`.
3. For a project with a CLI command, `ardoco-sad-code` and `ardoco-sad-sam` give the same file as the CLI. The file can be compared with `goldstandards/` in `sandman-example`.
4. Without a CLI command, the benchmark formats are disabled and the hint tells how to enable them.
5. The token never appears in arguments, logs, errors or the exported file.
6. Cancel in the save dialog changes nothing.
7. All texts exist in English and German.

## Rollback

Revert the commits of this task. The change is additive. No data file or route changes.

## Risks

* The benchmark formats depend on a CLI install. Mitigation: decision 5 and a clear hint.
* A large export in memory. Mitigation: write the body to the file as a stream if the response is large. Link counts today are in the thousands, so a plain write is enough for now.
* A user overwrites a gold standard file by mistake. Mitigation: the save dialog asks before overwriting (decision 9).

## Open items

* Importing a gold standard into the app and showing precision and recall is out of scope. A later task can add it.
* A REST route for the benchmark formats would remove the CLI dependency. A later task can add it.

## Constraints

* Do not edit `openwiki/`.
* Commit and push only to `develop` in the sandman repository. Commit the graphify update with each commit.
* No new dependency.
