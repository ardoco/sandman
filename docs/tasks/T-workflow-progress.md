# T-workflow-progress: more feedback while a workflow runs

## Goal

The user sees in which state a running workflow is.

## Decisions

- The first part needed no contract change. Step status, attempts, start and end times and `updatedAt` were enough.
- The view model is `services/dashboard/src/sandman_dashboard/progress.py`. The template filter `progress` uses it.
- Stalled hint: workflow `RUNNING`, current step `RUNNING`, no update for 600 s.
- The list page reads the steps of active workflows only.
- The CLI writes transitions to stderr. Stdout keeps the JSON result.
- Agent activity text is built with the optional `activity` member (contracts 0.8.0). See `docs/notes/workflow-progress.md`.

## Verification

- `uv run pytest services/dashboard/tests/test_workflows.py` (view model, all states, list).
- `uv run pytest tools/cli` (transition output).
