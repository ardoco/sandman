# T-workflow-progress: more feedback while a workflow runs

## Goal

The user sees in which state a running workflow is.

## Decisions

- No contract change. The existing workflow detail has all data needed (step status, attempts, start and end times, `updatedAt`).
- The view model is `services/dashboard/src/sandman_dashboard/progress.py`. The template filter `progress` uses it.
- Stalled hint: workflow `RUNNING`, current step `RUNNING`, no update for 600 s.
- The list page reads the steps of active workflows only.
- The CLI writes transitions to stderr. Stdout keeps the JSON result.
- Agent activity text is not built. See `docs/notes/workflow-progress.md`.

## Verification

- `uv run pytest services/dashboard/tests/test_workflows.py` (view model, all states, list).
- `uv run pytest tools/cli` (transition output).
