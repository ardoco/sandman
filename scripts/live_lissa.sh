#!/usr/bin/env bash
# LiSSA live re-run, variance pilot and contamination probe (docs/tasks/T2.4, protocol sections 4-6).
# Needs: running stack with an LLM endpoint, SANDMAN_URL, SANDMAN_TOKEN (admin).
#
#   live_lissa.sh rerun         INPUT_DIR GOLD PUBLISHED_F1 [RUNS]
#   live_lissa.sh pilot         INPUT_DIR GOLD [RUNS=5]
#   live_lissa.sh contamination INPUT_DIR GOLD NAMES_FILE [RUNS=3]
#
# INPUT_DIR: project directory with sandman.yaml kindRules (requirements + code).
# GOLD: LiSSA answer.csv. NAMES_FILE: one identifier per line (rename candidates).
# LLM_POLICY (default cloud-allowed) sets the project llmPolicy.
# Results go to experiments/live/<timestamp>-<mode>/.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SANDMAN_URL:?set SANDMAN_URL, e.g. http://127.0.0.1:8080}"
: "${SANDMAN_TOKEN:?set SANDMAN_TOKEN (admin token)}"
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"

mode=${1:?usage: live_lissa.sh rerun|pilot|contamination ...}; shift
input=$(cd "${1:?INPUT_DIR}" && pwd); gold=$(cd "$(dirname "${2:?GOLD}")" && pwd)/$(basename "$2")
out="experiments/live/$(date +%Y%m%d-%H%M%S)-$mode"; mkdir -p "$out"

# run_once DIR TAG -> prints F1 of one LiSSA run; writes $out/TAG.{csv,json}
run_once() {
  local dir=$1 tag=$2 id wf
  id="lissa-$tag-$(date +%s)"
  curl -fsS -X PUT "$SANDMAN_URL/v1/admin/projects/$id" \
    -H "Authorization: Bearer $SANDMAN_TOKEN" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$id\",\"llmPolicy\":\"${LLM_POLICY:-cloud-allowed}\"}" >/dev/null
  uv run sandman ingest --project "$id" --mode full --revision live "$dir" >/dev/null
  sleep 2
  wf=$(uv run sandman workflows --project "$id" | sort -t$'\t' -k4 | tail -1 | cut -f1)
  uv run sandman workflows --project "$id" --wait "$wf" --timeout 3600 >"$out/$tag.workflow.json" \
    || echo "workflow $wf of $id not SUCCEEDED" >&2
  uv run sandman export tracelinks --project "$id" --format lissa --approach lissa >"$out/$tag.csv"
  uv run sandman eval compare --format lissa --gold "$gold" --a "$out/$tag.csv" ${PUBLISHED:+--published "$PUBLISHED"} \
    >"$out/$tag.json"
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["a"]["f1"])' "$out/$tag.json"
}

# runs DIR PREFIX N -> F1 values, one per line
runs() { for i in $(seq "$3"); do run_once "$1" "$2-$i"; done; }
f1args() { sed 's/^/--f1 /' | tr '\n' ' '; }

case $mode in
rerun)
  PUBLISHED=${3:?PUBLISHED_F1}; n=${4:-1}
  runs "$input" live "$n" | tee "$out/f1.txt"
  echo "per-run reports: $out/live-*.json (published.delta and published.ci)" >&2 ;;
pilot)
  n=${3:-5}
  # shellcheck disable=SC2046
  runs "$input" pilot "$n" | tee "$out/f1.txt" | f1args | xargs uv run sandman eval pilot | tee "$out/pilot.json" ;;
contamination)
  names=$(cd "$(dirname "${3:?NAMES_FILE}")" && pwd)/$(basename "$3"); n=${4:-3}
  perturbed="$out/perturbed-input"; cp -R "$input" "$perturbed"
  # LiSSA links use file names, so perturb the code files flat and copy back by name.
  code=(); while IFS= read -r f; do code+=("$f"); done < <(cd "$input" && find src -type f \( -name '*.java' -o -name '*.py' \) | sort)
  [[ ${#code[@]} -gt 0 ]] || { echo "no code files under $input/src" >&2; exit 2; }
  dups=$(printf '%s\n' "${code[@]}" | xargs -n1 basename | sort | uniq -d)
  [[ -z $dups ]] || { echo "duplicate file names: $dups" >&2; exit 2; }
  abs=(); for f in "${code[@]}"; do abs+=("$input/$f"); done
  uv run sandman eval perturb --names "$names" --out "$out/flat" "${abs[@]}" >"$out/perturb.log"
  for f in "${code[@]}"; do cp "$out/flat/$(basename "$f")" "$perturbed/$f"; done
  runs "$input" orig "$n" | tee "$out/f1-original.txt" >/dev/null
  runs "$perturbed" pert "$n" | tee "$out/f1-perturbed.txt" >/dev/null
  uv run sandman eval contamination \
    --original $(cat "$out/f1-original.txt") --perturbed $(cat "$out/f1-perturbed.txt") | tee "$out/contamination.json" ;;
*) echo "unknown mode $mode" >&2; exit 2 ;;
esac
echo "results in $out" >&2
