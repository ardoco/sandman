# Password login that returns a token, and a login switch in the desktop app

Milestone: desktop app (follow-up of `T-desktop-connect.md`) · Effort M · Depends on: T-desktop-connect

Status: planned (2026-11-13)

## Context

`POST /v1/auth/login` checks a username and password and sets a session cookie. No `/v1/*` route accepts that cookie. Every route authenticates with `Authorization: Bearer` (`svc.authenticate`). The desktop app therefore cannot sign in with a password. It needs a token (`T-desktop-connect.md`).

Accepting the cookie on `/v1/*` would expose all state-changing routes to CSRF. This task adds a route that returns a token instead.

## Goal

A user signs in from the app with a username and password. The server returns a personal token. The app saves the token as it does today. The connect form offers two ways to sign in: username and password, or token. A button switches between them.

## Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Route | New `POST /v1/auth/login-token`. `POST /v1/auth/login` (cookie) does not change. |
| 2 | Request | `{ username, password, name?, projectId?, maxRole?, expiresAt? }`, `extra="forbid"`. |
| 3 | Defaults | `name` defaults to `desktop`. `expiresAt` defaults to 90 days after now. The caller may set an earlier date. A later date than 365 days is rejected (422). |
| 4 | Checks | Same as `login`: rate limiter keys (user name and client address), `verify_password`, `must_change_password` returns 403 with the text that `authenticate` uses ("password change required"), disabled users are rejected as in `verify_password`. |
| 5 | Token | Created with `store.create_token`. The response is the token document plus `secret`, as `POST /v1/auth/tokens` returns it. The server sets no cookie. |
| 6 | Scope | `projectId` and `maxRole` work as in `NewToken`. The app sends neither by default. |
| 7 | Desktop switch | One button toggles the connect form between "Username and password" and "Token". The app remembers the last choice in memory only. |
| 8 | Password handling | The password stays in the webview field, goes to a Rust command, and is never stored, logged or returned. The field is cleared after the call. |
| 9 | Token name | `desktop <host>` (host of the URL), so the user can revoke it in the console. |

## Steps

Each step ends with its check. Keep the handoff note `.work/T-desktop-password-login.md` up to date.

1. Server: model `LoginToken`, route `login-token` in `services/query-service/src/sandman_query/app.py`, and the logic in `service.py` (reuse `login`'s limiter and password check; factor out only what both routes share).
   Check: tests in `services/query-service/tests/test_api.py`: success (the secret works as bearer on `/v1/auth/me`), wrong password (401, limiter counts), 6th failure (429 with `Retry-After`), `must_change_password` (403), disabled user, default expiry (about 90 days), expiry above 365 days (422), unknown field (422), `/v1/auth/login` unchanged.
2. Contract: add the route to `contracts/openapi/query-service.yaml`. Run the contract checks.
   Check: `python scripts/validate_contracts.py` (or the repo's contract make target) passes. Keep every route in sync with the existing route tests.
3. Desktop Rust: command `login_connect(url, username, password)` in `api.rs`. Validate the URL as `connect_system` does. Call `/v1/auth/login-token`. On 200, run the same project listing and upsert as `connect_system` with the returned secret. Return the same result shape. Do not put the password or the secret in any error text.
   Check: unit test for the request body builder (name `desktop <host>`) and the error mapping (401, 403 password change, 429). Reuse `plan_connect`.
4. Desktop UI (`start.ts`, `logic.ts`): add the switch button in the top block. In mode "Username and password" the fields are URL, Username, Password. In mode "Token" the fields are URL, Token. The URL field keeps its value when the user switches.
   Check: vitest for the pure helpers (field set per mode, error text for 429 and 403). Run `npm test` and `npm run build`.
5. Docs: `docs/user/desktop-app.md` (section "Connect to a running system": both ways, token name, 90-day expiry, how to revoke), the REST docs that list auth routes. Controlled language rules apply.
   Check: docs link check of CI passes.
6. Full verification: `uv run pytest services/query-service`, `make lint`, `cargo test`, `cargo clippy -- -D warnings`, `cargo fmt --check`, `npm test`, `npm run build`.

## Acceptance criteria

1. A valid username and password in the app lists the projects of the system and adds them, with no manual token.
2. The new token appears in the console token list with the name `desktop <host>` and an expiry date.
3. A wrong password, a locked-out user and a user with a required password change each show a clear message.
4. The session cookie login of the console behaves as before.
5. The password and the token never appear in logs, errors or `projects.json`.
6. The switch button changes the form in both directions and keeps the URL.

## Rollback

Revert the commits of this task. The new route is additive. The token-only connect keeps working.

## Risks

* The password travels from the app to the server. Mitigation: the existing `http` warning from `valid_url`, shown before the password is sent when the host is not loopback. Block the password login over `http` on a non-loopback host.
* A long-lived token from a password login. Mitigation: default expiry of 90 days, a maximum of 365 days, a clear name for revocation.
* Brute force. Mitigation: the limiter of `login` applies unchanged.

## Constraints

* Do not edit `openwiki/`.
* Commit and push only to `develop`. Commit the graphify update with each commit.
* No new dependency.
