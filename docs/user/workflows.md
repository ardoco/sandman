# Workflows

A workflow is the processing that follows a change: extract elements, recover trace links, detect inconsistencies. The orchestrator creates it, runs its steps on the agents, and keeps the state. Each project runs one step at a time.

## What starts automatically

An ingestion that created or reactivated versions, or deleted artifacts, produces one workflow with these steps (trigger kind `event`):

1. `extract-artifact-versions` (extraction agent) for the new and reactivated versions.
2. `recover-trace-links` (TLR agent) for each configured kind pair, changed versions against the project's current counterparts. The shipped pairs (`config/orchestrator/rules.yaml`) are `requirements` to `code`, `architecture-doc` to `architecture-model`, `architecture-doc` to `code` and `architecture-model` to `code`. Every enabled approach of `config/tlr/approaches.yaml` that supports a pair runs for it.
3. `detect-inconsistencies` (inconsistency agent) for the affected artifacts. After a deletion it runs for the deleted artifacts too, so their findings resolve.

If a project has an architecture document but no architecture model, a derivation step first creates a [derived model](derived-models.md). A trace link review by a triager starts a new detection step for the affected artifacts ([trace-links-and-findings.md](trace-links-and-findings.md#review-trace-links)).

Link-recovery and detection steps that are still waiting are merged when new work for the same project arrives (debounce `debounceSeconds`, 10 seconds). The mini example takes about 25 seconds from upload to `SUCCEEDED`. An ingestion that changes nothing starts no workflow.

## Statuses

`PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELED`, `BLOCKED`. A workflow is `RUNNING` while any step is pending or running. Otherwise it takes the first of `BLOCKED`, `FAILED`, `CANCELED` among its steps, else `SUCCEEDED`. `BLOCKED` means a step stopped because a budget limit was exceeded and needs an operator; no built-in component uses LLM budgets today.

`SUCCEEDED` only says that every step ran. A step can succeed with a `partial` run (a plugin was unreachable, a file fell back to a file-level element); check the run status as described in [ingesting-artifacts.md](ingesting-artifacts.md).

## Watching

Console: **Workflows** on the project row (`/console/projects/<id>/workflows`) lists the latest 20 workflows with id, trigger, status and times. The trigger shows `event` or `manual (user:<name>)`. For a running workflow, the list shows the finished steps, the current skill and its activity text.

CLI:

```sh
uv run sandman workflows --project demo
# wf_faff0b3c9533410eac03b6bfeb87a808   SUCCEEDED   event   2026-10-08T12:05:47Z   (tab separated; first page only)

uv run sandman workflows --project demo --wait wf_faff0b3c9533410eac03b6bfeb87a808 [--timeout 600] [--poll 2]
```

Dashboard: the workflow page (`/workflows/<id>`) shows the step number, a progress bar, a timeline of all steps, the current step with its agent and elapsed time, attempts, and the time since the last update. If a running workflow has no update for 10 minutes, the page shows "Stalled?". The page refreshes every 5 seconds while the workflow is `PENDING` or `RUNNING`. The workflows list shows finished and total steps.

`--wait` prints each step change and workflow change to stderr, for example `[   12s] step 2/3 recover-trace-links (tlr-agent): RUNNING attempt 1`. While a step runs, the line also shows the activity text of the agent, for example `step 2/3 recover-trace-links: calling LLM for link classification 40/120`. It polls until the workflow is `SUCCEEDED`, `FAILED`, `CANCELED` or `BLOCKED`, prints the full workflow as JSON, and exits with 0 only for `SUCCEEDED`; it exits with an error after `--timeout` seconds (default 600). Use it in scripts after `sandman ingest`.

REST: `GET /v1/projects/{id}/workflows?status=FAILED&limit=20&cursor=...` (newest first) and `GET /v1/workflows/{workflowId}`. The detail has `steps`: agent, skill, status, `attempts`, `input`, `result`, `error` (`code`, `message`, `retryable`), `startedAt`, `endedAt`. A `RUNNING` step can have `activity` (`text`, `at`): a short text of the current work. Agents update it at most every 2 s. Old agents send no text, so `activity` is absent.

```sh
curl -s -H "Authorization: Bearer $SANDMAN_TOKEN" http://127.0.0.1:8080/v1/workflows/wf_...
```

Any role of the project may read workflows.

## Triggering

Maintainers and admins can start a workflow by hand: in the console press `full`, `extract`, `tlr` or `detect` under **Trigger**, or call the API:

```sh
curl -s -X POST -H "Authorization: Bearer $SANDMAN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"type":"full"}' http://127.0.0.1:8080/v1/projects/demo/workflows      # 202
```

| Type | Runs |
|---|---|
| `full` | extraction of all current versions; then the chain continues as after an ingestion |
| `extract` | extraction of all current versions only |
| `tlr` | link recovery for all current versions of each kind pair only |
| `detect` | detection for the whole project only |

Optional body members: `params` (merged into the step input, for example `{"approaches":["stub-name-match"]}` for `tlr`) and `force`. A request with no matching current versions (an empty project) is rejected with `400 nothing to run`. A manual run is the way to reprocess after a configuration change or after a failure, because re-sending an unchanged snapshot starts nothing.

## Failures, retries, cancel

- A step that fails with a retryable error (agent unreachable, timeout, lost task) is retried up to 3 attempts with waits of 30 s, 2 min and 10 min. A non-retryable error fails the step at once. Step timeouts: extraction 15 min, link recovery 60 min, detection 30 min.
- A failed step ends the chain; later steps do not run and the workflow is `FAILED`. The error is in the step's `error` member of the workflow detail.
- A step waiting for a retry still holds the project, so new steps of that project queue behind it.
- Cancel: the orchestrator can cancel a workflow, but no console page, CLI command or user API route exposes it today (`POST /v1/workflows/{id}/cancel` on the query service returns 404). Operators can only wait for the step timeout.
