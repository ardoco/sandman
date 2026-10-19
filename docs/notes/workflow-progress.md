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

## Decided and built: current activity text (contracts 0.8.0)

1. Agents call `progress(text)` of the agent lib. The lib sends the text as the A2A `TaskStatus.message` with state `TASK_STATE_WORKING`.
2. The lib sends at most one update every 2 s. It drops the other updates.
3. The orchestrator copies the last text and its time into the columns `activity_text` and `activity_at` of `steps` (migration 004). It cuts the text at 200 characters.
4. `WorkflowDetail.steps[*].activity` (`text`, `at`) is optional. The orchestrator sets it only while the step is `RUNNING`. Old agents and old clients keep working.
5. The dashboard (workflow detail and workflows list) and `sandman workflows --wait` show the text.
6. The agents tlr-agent (lissa, ardoco, ardocode), inconsistency-agent and extraction-agent send the text.
