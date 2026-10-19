#!/usr/bin/env bash
set -euo pipefail

: "${SANDMAN_URL:?input url is empty}"
: "${SANDMAN_PROJECT:?input project is empty}"
if [ -z "${SANDMAN_TOKEN:-}" ]; then
  echo "::error::input token is empty. Pass a repository secret. Secrets are not available to pull requests from forks."
  exit 1
fi
echo "::add-mask::$SANDMAN_TOKEN"
export SANDMAN_URL SANDMAN_TOKEN

action_root="$(cd "$(dirname "$0")/../.." && pwd)"
read -r -a sandman <<<"${SANDMAN_CMD:-uv run --locked --no-dev --package sandman-cli --project $action_root sandman}"

mode="${INPUT_MODE:-full}"
dir="$(cd "${INPUT_PATH:-.}" && pwd)"
revision="${INPUT_REVISION:-}"
wait_for="${INPUT_WAIT:-true}"
timeout="${INPUT_TIMEOUT:-600}"
poll="${SANDMAN_POLL:-2}"

case "$mode" in full | partial) ;; *) echo "::error::mode must be full or partial"; exit 1 ;; esac
case "$timeout" in '' | *[!0-9]*) echo "::error::timeout must be whole seconds"; exit 1 ;; esac

if [ "$mode" = partial ]; then
  : "${INPUT_BASE:?input base is empty (partial mode needs a base commit)}"
  stage="$(mktemp -d)"
  trap 'rm -rf "$stage"' EXIT
  [ -f "$dir/sandman.yaml" ] && cp "$dir/sandman.yaml" "$stage/"
  while IFS= read -r -d '' f; do
    [ -f "$dir/$f" ] || continue
    mkdir -p "$stage/$(dirname "$f")"
    cp "$dir/$f" "$stage/$f"
  done < <(cd "$dir" && git diff -z --name-only --diff-filter=d "$INPUT_BASE" HEAD -- .)
  dir="$stage"
fi

args=(ingest --project "$SANDMAN_PROJECT" --mode "$mode")
[ -n "$revision" ] && args+=(--revision "$revision")
result="$("${sandman[@]}" "${args[@]}" "$dir")"
ingestion_id="$(jq -r .ingestionId <<<"$result")"
changed="$(jq '(.createdVersionIds | length) + (.reactivatedVersionIds | length) + (.deletedArtifactIds | length)' <<<"$result")"
created_at="$(jq -r '.createdAt[0:19]' <<<"$result")"

workflow_id="" status="none"
if [ "$changed" -gt 0 ]; then
  deadline=$((SECONDS + 60))
  while [ -z "$workflow_id" ]; do
    workflow_id="$("${sandman[@]}" workflows --project "$SANDMAN_PROJECT" | awk -F'\t' -v t="$created_at" 'substr($4, 1, 19) >= t { print $1; exit }')"
    [ -n "$workflow_id" ] && break
    [ "$SECONDS" -ge "$deadline" ] && { echo "::error::no workflow started for ingestion $ingestion_id"; exit 1; }
    sleep "$poll"
  done
  status="STARTED"
fi

rc=0
if [ -n "$workflow_id" ] && [ "$wait_for" = true ]; then
  wf="$("${sandman[@]}" workflows --project "$SANDMAN_PROJECT" --wait "$workflow_id" --timeout "$timeout" --poll "$poll")" || rc=$?
  [ -n "$wf" ] && status="$(jq -r .status <<<"$wf")"
fi

{
  echo "ingestion-id=$ingestion_id"
  echo "workflow-id=$workflow_id"
  echo "status=$status"
} >>"${GITHUB_OUTPUT:-/dev/null}"
[ "$rc" -eq 0 ] || echo "::error::workflow $workflow_id ended with status $status"
exit "$rc"
