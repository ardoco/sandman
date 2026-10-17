# Workflow progress: decisions and proposal

## Done

- Dashboard workflow detail shows step i of n, a progress bar, a step timeline, elapsed time, attempts and time since the last update.
- A step in `PENDING` with attempts above 0 is shown as a retry.
- A `RUNNING` workflow with no update for 10 minutes shows a "Stalled?" hint (`STALL_SECONDS` in `progress.py`). The step timeouts are 15, 60 and 30 minutes.
- Error codes get a plain sentence. The raw code and message stay in brackets.
- The workflows list shows finished/total steps and the current skill. The dashboard reads the steps of active workflows only, one call each.
- `sandman workflows --wait` prints step and workflow transitions to stderr. Stdout keeps the final JSON. The output has no colors and no cursor control, so it is the same on a TTY and in a pipe.
- Polling stays at 5 s and runs only while the workflow is `PENDING` or `RUNNING`.
- The project page has no indicator. It does not read workflows today.

## Not done: current activity text

The orchestrator stores no text from the agents. It knows only the step status and the A2A task state. A message such as "linking 40 of 120 elements" needs new data on the contract path.

## Proposal

1. Agents set a status message on the A2A task update (`TaskStatus.message`). This field exists in A2A and needs no change to it.
2. The orchestrator copies the last message text and its time into new nullable columns of `steps` (migration 004).
3. The orchestrator adds an optional `activity` object (`text`, `at`) to the step in `WorkflowDetail`. Existing clients ignore it.
4. The dashboard and the CLI show `activity.text` next to the current step.

Follow the contract rules in `docs/stage-2-contracts-repository.md`: change `contracts/openapi/orchestrator.yaml` first, keep the member optional, and extend the contract tests. Do this in a separate task.
