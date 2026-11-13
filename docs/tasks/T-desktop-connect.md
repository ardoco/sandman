# Desktop app: connect to a running system from the start screen

Milestone: desktop app (follow-up of `T-desktop-app.md`) · Effort M · Depends on: T-desktop-app

Status: planned (2026-11-13)

## Context

The desktop app works on projects. A remote project needs a name, an id, a URL and a token. The user enters them in the "New project" form. The user must know the project id. A token can be limited to one project. A user can therefore own several tokens for one system.

The start screen shows only the project list. It has no entry point for "connect to a running system" and none for "run locally".

## Findings (2026-11-13, source)

1. `GET /v1/auth/me` and `GET /v1/projects` accept `Authorization: Bearer <token>`. `GET /v1/projects` returns `id`, `name`, `llmPolicy`, `createdAt` and `role` for each project that the token can read. A token limited to one project returns only that project (`services/query-service/src/sandman_query/app.py`, `service.py`).
2. `POST /v1/auth/login` returns only a session cookie. No `/v1/*` route accepts that cookie. A username and password cannot give the app a token. Token-only is the scope of this task.
3. The app keeps one token per project id in the keychain (`token:{projectId}`, `api.rs`). `Project` has `mode`, `url`, `checkout`. `store::valid_url` validates the URL and returns a warning for `http` on a non-loopback host.
4. `check_token` validates a token with `/v1/auth/me` and saves it. The web view calls it through `saveToken`.

## Goal

The start screen has two blocks. The top block connects to a running system with a URL and a token. A divider follows. Below it, a button "Run locally" starts the local setup. Connecting lists the projects of the system and adds them. The user can add a second token for another project of the same system with the same form.

## Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Data model | Keep `Project`. No new file, no new field. A connect adds one remote `Project` per listed project. |
| 2 | Credentials | Token only. No username and password. |
| 3 | Token storage | One keychain entry per project (`token:{id}`), as today. The connect saves the token for each project that it adds. |
| 4 | Same id, other system | If a project with this id exists with another URL, or in mode `local`, skip it. Report it in `skipped` with the reason. Never overwrite it. |
| 5 | Same id, same URL | Update the name and the token. Keep `lastUsed`. |
| 6 | Second token | Use the same form again. A token limited to project B adds B. No extra dialog. |
| 7 | Missing token | A project row without a token shows "Token needed". A click opens a token prompt. The prompt uses the saved URL. |
| 8 | Prefill | The URL field starts with the URL of the project used last in mode `remote`. The token field starts empty. |
| 9 | Run locally | If a local project exists, open the most recent one. Otherwise open the new-project form with mode `local` selected. |
| 10 | Link import (`sandman://connect`) | Out of scope. Later task with a console change. |

## Steps

Each step ends with its check. Keep the handoff note `.work/T-desktop-connect.md` up to date.

1. Rust command `connect_system(url, token)` in `api.rs`.
   - Validate the URL with `store::valid_url`. Validate the token as `check_token` does.
   - Call `/v1/auth/me`. Return the status and message if it is not 200.
   - Call `/v1/projects`. Upsert one remote `Project` per entry (decisions 4 and 5). Save the token for each added project.
   - Return `{ username, added: [id], updated: [id], skipped: [{id, reason}], warning }`. Register the command in `lib.rs`.
   - Check: unit tests with a local mock HTTP server, or a pure function `plan_connect(existing, listed, url)` with tests for decisions 4 and 5. The tests cover an empty list, a restricted token (one project), an id clash with a local project, and an id clash with another URL.
2. Start screen (`src/views/start.ts`, `style.css`).
   - Top block: fields URL and Token, button Connect. Show the result (added, updated, skipped with reason, warning) below the form.
   - Divider. Button "Run locally" (decision 9).
   - Project list, Open, Remove, Keys, New project: unchanged, below the divider.
   - Rows without a token show "Token needed" and open the token prompt (decision 7, through `saveToken`).
   - Check: a vitest test for the pure helpers that you add to `logic.ts` (result text, prefill URL, local project choice).
3. Docs: update `docs/user/desktop-app.md` (new section "Connect to a running system", token tips: one token per project, a limited token adds one project) and `apps/desktop/README.md` if it lists the start screen. Use the controlled language rules.
   - Check: `make lint` and the docs link check in CI pass.
4. Verification: `cargo test`, `cargo clippy -- -D warnings`, `cargo fmt --check` in `apps/desktop/src-tauri`. `npm test` and `npm run build` in `apps/desktop`. Run the app against a local stack if Docker is available (`make up`). If not, say so.

## Acceptance criteria

1. With a valid URL and token, Connect lists the projects of the system and adds them. The user needs no project id.
2. A token limited to one project adds only that project. A second Connect with a second token adds the second project. The first one stays usable.
3. A wrong token shows the message of `tokenMessage`. No project is added.
4. A clash (decision 4) never overwrites a local project. The user sees the reason.
5. "Run locally" opens the local setup or the last local project.
6. Tokens never appear in logs, errors or `projects.json`.
7. All checks of step 4 pass.

## Rollback

Revert the commits of this task. `Project` and the keychain layout do not change, so saved projects keep working.

## Risks

* An `http://` URL on a non-loopback host sends the token without encryption. Mitigation: the existing warning from `valid_url`, shown in the connect result.
* A project id clash between two systems. Mitigation: decision 4. The id stays the key of the app.

## Constraints

* Do not edit `openwiki/`.
* Commit and push only to `develop`. Commit the graphify update with each commit.
* No new dependency.
