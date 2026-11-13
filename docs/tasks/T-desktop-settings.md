# Desktop app: settings screen with app settings, project settings and German

Milestone: desktop app (follow-up of `T-desktop-connect.md`) · Effort L · Depends on: T-desktop-app

Status: planned (2026-11-13)

## Context

The app has no settings menu. It has one flat page `src/views/settings.ts`. That page is the last project tab "Settings". It shows the token, the CLI command and, for a local project, the LLM keys and the CoreNLP mode.

The app has no app preferences. The theme follows the operating system (`prefers-color-scheme` in `src/style.css`). The font size is fixed (`14px`). All UI texts are English string literals in `src/views/*.ts`, `src/logic.ts` and in error texts of `src-tauri/src/*.rs`. The CSP (`style-src 'self'`) forbids inline styles.

## Goal

A settings screen with two tabs. The tab "General" holds the app settings: language, theme and font size. The tab "Project" holds the settings of the open project. The language of the UI is English or German.

## Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Entry points | A "Settings" button on the start screen and in the project header. The project tab "Settings" is removed. Its content moves to the tab "Project". |
| 2 | Tabs | "General" and "Project". On the start screen, or when no project is open, "Project" is disabled with the hint "Open a project first". The header button opens the screen with "Project" selected. The start screen opens it with "General" selected. |
| 3 | App settings | `language` (`system`, `en`, `de`), `theme` (`system`, `light`, `dark`), `fontSize` (`small` 13px, `normal` 14px, `large` 16px). Defaults: `system`, `system`, `normal`. |
| 4 | Storage | New file `settings.json` in the app config directory, next to `projects.json`. New struct `AppSettings` in `store.rs`. Unknown fields are ignored. An invalid or missing file gives the defaults. `Project` does not change. Commands `load_app_settings` and `save_app_settings`. |
| 5 | Apply | Settings apply at once, with no restart. The web view sets `data-theme` and `data-font` on `<html>`. CSS selectors use them. No inline style (CSP). A change saves at once and shows a toast. |
| 6 | Theme CSS | The dark variables move to a rule for `:root[data-theme="dark"]`. The media query applies only if `data-theme` is `system`. |
| 7 | Language, system | `system` reads `navigator.language`. A value that starts with `de` gives German. All other values give English. |
| 8 | i18n | A small module `src/i18n.ts` with `t(key, params?)`. Two catalogues, `en.ts` and `de.ts`, as typed objects. The type of `de` equals the type of `en`, so a missing key fails `npm run typecheck`. No new dependency. |
| 9 | Language switch | A language change re-renders the current screen. `<html lang>` is set. The window title uses the language (`windowTitle` in `logic.ts`). |
| 10 | Scope of translation | All texts of the web view: labels, buttons, headings, tab names, toasts, empty states, hints. Not translated: API names, route names, project ids, approach ids (`artemis`, `transarc`), CLI output, the text of server errors, and log lines. |
| 11 | Rust error texts | The commands return stable error codes with parameters. The web view maps them to texts. Existing English texts stay as the fallback. Do this only for the errors that the UI shows today (`tokenMessage`, `loginMessage`, URL and checkout validation, stack errors). |
| 12 | Dates and numbers | Use `Intl.DateTimeFormat` and `Intl.NumberFormat` with the selected language. |
| 13 | Tab "General" content | Language, theme, font size. The default checkout folder for "Run locally". The list of saved systems (URL, number of projects) with "Forget" (removes the projects of that URL and their keychain tokens, after a confirm). The app version. |
| 14 | Tab "Project" content | The current page, in groups: Connection (URL, token), Local stack (CLI command, CoreNLP mode, keys), Links (console, dashboard). The behaviour is unchanged. |
| 15 | Docs | `docs/user/desktop-app.md` gets a section "Settings". Language names in the app: "English" and "Deutsch". The docs stay English. |

## Steps

Each step ends with its check. Keep the handoff note `.work/T-desktop-settings.md` up to date. Commit each step.

1. Store: `AppSettings` and the two commands in `store.rs` and `api.rs` (or `lib.rs`), registered in `lib.rs`. Types and wrappers in `src/api.ts`.
   Check: Rust tests: defaults, round trip, invalid JSON gives defaults, unknown field is ignored, invalid enum value gives the default for that field. `cargo test`.
2. Theme and font size: `data-theme` and `data-font` in `style.css` (decision 6), `applySettings(settings)` in `logic.ts`, called at start in `main.ts`.
   Check: vitest for `applySettings` on a fake root element (attributes set, `lang` set). `npm run build`. No inline style in the output (`grep 'style=' dist`).
3. Settings screen: new `src/views/appsettings.ts` with the two tabs. Move the content of `settings.ts` into the tab "Project" without behaviour change. Add the entry points (decision 1). Remove the project tab "Settings" from `TABS`. Add the "Settings" button to `start.ts` and `project.ts`.
   Check: vitest for the pure helper that picks the first tab and the disabled state. `npm run typecheck` and `npm run build`.
4. i18n infrastructure: `i18n.ts`, `en.ts`, `de.ts`, language choice (decisions 7 and 9), `Intl` helpers (decision 12). Translate the settings screen and the start screen first.
   Check: vitest: `t` with params, fallback to English for a missing runtime key, language `system` with `de-DE` and `en-US`, equal key sets of `en` and `de` (a test that compares the key lists). `npm run typecheck`.
5. Translate all remaining views and toasts (decision 10). Add the error codes (decision 11). Keep one German term per concept. Write a glossary at the top of `de.ts` (for example "Projekt", "Schlüssel", "Trace-Link", "Befund"). Keep English product terms that have no common German word.
   Check: the key-set test from step 4 passes. A test scans `src/views/*.ts` for string literals passed to `h(...)` text positions that are not `t(...)`. Allow a short list of exceptions in the test. `npm test`.
6. "Forget" for saved systems and the default checkout folder (decision 13).
   Check: Rust test for the removal of all projects of one URL and the list of tokens to delete (pure function, as `plan_connect`). `cargo test`.
7. Docs (decision 15) and the `apps/desktop/README.md` list of screens. Controlled language rules apply.
   Check: docs link check of CI passes.
8. Full verification: `cargo test`, `npm test`, `npm run typecheck`, `npm run build`, `./guard.sh`, `make lint` and `make contracts` at the repository root. Run the app if possible and switch language, theme and font size. If the run is not possible, say so.

## Acceptance criteria

1. The screen "Settings" opens from the start screen and from a project. It has the tabs "General" and "Project".
2. Language, theme and font size change the UI at once and persist after a restart.
3. With language `system` and an operating system in German, the UI is German. With any other language, it is English.
4. No UI text of the web view stays English in German mode, except the items in decision 10.
5. A missing German key fails `npm run typecheck`. A missing runtime key falls back to English.
6. The tab "Project" works as the old page: token, CLI command, keys, CoreNLP mode, console and dashboard links.
7. `settings.json` holds no token, key or password.
8. The CSP is unchanged. The built app has no inline style.

## Rollback

Revert the commits of this task. `projects.json` and the keychain layout do not change. An unused `settings.json` does no harm.

## Risks

* The translation touches every view. Mitigation: steps 4 and 5 are separate commits. The key-set test and the literal scan prevent drift.
* Rust error texts. A mapping on the web side can miss a text. Mitigation: English fallback, tests for the errors that the UI shows.
* German text is longer than English. Mitigation: check tab names, buttons and tables in the running app. Allow wrapping (`flex-wrap` is set on `nav` and `.row`).
* "Forget" removes tokens. Mitigation: a confirm that names the URL and the number of projects.

## Open items

* Other languages are out of scope. The catalogue layout allows a third file later.
* Auto-start choices, update checks and telemetry are out of scope.

## Constraints

* Do not edit `openwiki/`.
* Commit and push only to `develop`. Commit the graphify update with each commit.
* No new dependency.
