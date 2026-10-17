#!/usr/bin/env bash
# Check B of docs/user/evaluation.md: live stack links == standalone ArDoCo links.
# Needs: running stack, SANDMAN_URL, SANDMAN_TOKEN (admin), Docker (for the Maven test).
# Usage: scripts/live_equality.sh [--skip-mvn]
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SANDMAN_URL:?set SANDMAN_URL, e.g. http://127.0.0.1:8080}"
: "${SANDMAN_TOKEN:?set SANDMAN_TOKEN (admin token)}"
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"

out="$PWD/out"; fixtures=services/extraction-agent/tests/fixtures/benchmark
suffix=$(date +%s)
# project:model file:kind-rule glob
cases=("mediastore:ms.repository:pcm" "teastore:teastore.repository:pcm")

mkdir -p "$out"
if [[ "${1:-}" != "--skip-mvn" ]]; then
  SANDMAN_BENCHMARK_CSV_DIR="$out" mvn -q -pl services/tlr-agent/approach-ardoco -am \
    -Dtest=BenchmarkEquivalenceTest -Dsurefire.failIfNoSpecifiedTests=false test
fi

fail=0
for c in "${cases[@]}"; do
  IFS=: read -r p model dir <<<"$c"
  id="$p-$dir-$suffix"
  echo "== $id" >&2
  curl -fsS -X PUT "$SANDMAN_URL/v1/admin/projects/$id" \
    -H "Authorization: Bearer $SANDMAN_TOKEN" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$id\",\"llmPolicy\":\"local-only\"}" >/dev/null
  uv run sandman ingest --project "$id" --mode full --revision benchmark \
    --kind-rule '*.txt=architecture-doc' --kind-rule "$dir/**=architecture-model" \
    "$fixtures/$p" >/dev/null
  sleep 2
  wf=$(uv run sandman workflows --project "$id" | sort -t$'\t' -k4 | tail -1 | cut -f1)
  uv run sandman workflows --project "$id" --wait "$wf" >/dev/null || echo "workflow $wf not SUCCEEDED (other approaches may fail)" >&2
  uv run sandman export tracelinks --project "$id" --format ardoco-sad-sam --approach ardoco > "$out/$p-live.csv"
  if uv run sandman eval equal --format ardoco-sad-sam \
      --expected "$out/$p-$model.standalone.csv" --actual "$out/$p-live.csv"; then
    echo "PASS $p"
  else
    echo "FAIL $p" >&2; fail=1
  fi
done
exit $fail
