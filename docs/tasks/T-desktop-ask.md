# Desktop app: full Ask tab with strategy, query-only mode, earlier state and history

Milestone: desktop app (follow-up of `T-desktop-export.md`) · Effort S · Depends on: T-desktop-export

Status: planned (2026-11-13)

## Context

The tab Ask of the desktop app sends only `projectId` and `question` to `POST /v1/query/nl`. It shows the answer, the rows, the Cypher and the warnings. The REST API and the console do more. They accept `strategy`, `mode`, `asOf` and `asOfRevision`. They return the resolved strategy, the classification, the evidence, the steps, the attempts and the `asOf` information.

## Goal

The user asks every kind of question from the app that the console allows. The user sees how the system answered, and which state of the project it read.

## Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Transport | The existing generic `api` command. No Rust change. |
| 2 | Form | Question (at least 3 characters), strategy select (`auto` default), checkbox "Show the query only" (`mode: cypher-only`), group "Earlier state" with the choice none, time or revision. |
| 3 | Earlier state | Time (`datetime-local`) goes out as ISO UTC in `asOf`. Revision goes out as `asOfRevision`. The body never has both. |
| 4 | Examples | Five chips for the fixed patterns. A chip with a placeholder fills the input and leaves the cursor to the user. |
| 5 | Result | Answer, resolved strategy, classification, rows (with a `truncated` note), Cypher with a Copy button, evidence list, steps and attempts in `details`, warnings, `asOf` line. |
| 6 | History | The last 10 questions per project, in memory only, without duplicates. A click refills the form. Nothing goes to disk. |
| 7 | Errors | 401 and 403 use `tokenMessage`. 422 retention or unknown revision, 501 disabled strategy and 409 missing embeddings get a clear text with the server detail. No raw JSON. |
| 8 | i18n | New texts through `t()`, English and German. Error texts use the `err.*` keys. |
| 9 | Contract | The `queryNl` entry in `CALLS` lists the new body and response fields. `contract.test.ts` keeps it in sync with the OpenAPI file. |

## Steps

Each step ends with its check. Keep the handoff note `.work/T-desktop-ask.md` up to date. Commit each step.

1. Helpers in `logic.ts`: `buildAskBody`, `pushHistory`, `askErrorMessage`, `asOfSummary`. Update `CALLS`.
   Check: vitest for the at-most-one rule, ISO conversion, history cap and dedupe, and error mapping. `npm test`, `npm run typecheck`.
2. View `src/views/ask.ts`, CSS classes, `en.ts` and `de.ts` entries.
   Check: key-set test and literal-scan test. `npm run build`.
3. Docs: `docs/user/desktop-app.md` (row and section "Ask a question"), `apps/desktop/README.md` if it lists views.
   Check: links valid. `make lint`, `make contracts`.

## Acceptance criteria

1. The body has at most one of `asOf` and `asOfRevision`.
2. The tab shows strategy, classification, evidence, steps, attempts and the `asOf` line when the server sends them.
3. 422, 501, 409, 401 and 403 show a clear text, in English and German.
4. The history holds 10 entries per project and is gone after a restart.
5. All texts exist in English and German.

## Rollback

Revert the commits of this task. The change is additive. No route or data changes.

## Risks

* A browser time without a zone differs from the server time. Mitigation: convert the local time to UTC before sending.
* The response may omit fields for some strategies. Mitigation: render each block only if present.

## Constraints

* Do not edit `openwiki/`.
* Commit and push only to `develop`. Commit the graphify update with each commit.
* No new dependency.
