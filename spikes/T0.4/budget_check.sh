#!/usr/bin/env bash
# T0.4 check 5: budget exhaustion and rate limit on a throwaway Bifrost (port 18081).
# Uses the provider keys from deploy/secrets/gateway.env; never prints keys. Run from the repository root:
#   bash spikes/T0.4/budget_check.sh
set -euo pipefail
ROOT=$(pwd)
T=$(mktemp -d)
trap 'docker compose -p t04budget -f "$T/compose.yml" down -v >/dev/null 2>&1 || true; rm -rf "$T"' EXIT

# test config: query key budget 0.000001 USD (part A); rate limit of the tlr key 2 requests/min (part B)
python3 - "$T/config.json" <<'PY'
import json, sys
c = json.load(open("deploy/gateway/config.json"))
for b in c["governance"]["budgets"]:
    if b["id"] == "b-query":
        b["max_limit"] = 0.000001
for r in c["governance"]["rate_limits"]:
    if r["id"] == "rl-heavy":
        r["request_max_limit"] = 2
json.dump(c, open(sys.argv[1], "w"))
PY

cat > "$T/compose.yml" <<YAML
services:
  gw:
    image: maximhq/bifrost:v2.2.6
    environment:
      APP_HOST: 0.0.0.0
      APP_PORT: "8080"
    env_file:
      - '$ROOT/deploy/secrets/gateway.env'
    volumes:
      - type: bind
        source: '$T/config.json'
        target: /app/data/config.json
        read_only: true
    ports:
      - '127.0.0.1:18081:8080'
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/health"]
      interval: 5s
      retries: 10
YAML

docker compose -p t04budget -f "$T/compose.yml" up -d --wait

call() {  # $1 = key file; prints status line, rate-limit headers and the response body
  curl -s -D "$T/headers" -o "$T/body" -w 'HTTP %{http_code}\n' http://127.0.0.1:18081/openai/chat/completions \
    -H "Authorization: Bearer $(cat "$1")" -H 'content-type: application/json' \
    -d '{"model":"openai/gpt-4.1-nano","messages":[{"role":"user","content":"Say ok"}],"max_tokens":3}'
  grep -iE 'retry-after|ratelimit' "$T/headers" || true
  head -c 400 "$T/body"; echo; echo
}

echo "== A: budget (query key, 3 calls)"
for _ in 1 2 3; do call deploy/secrets/llm_key_query; done
echo "== B: rate limit (tlr key, 3 quick calls)"
for _ in 1 2 3; do call deploy/secrets/llm_key_tlr; done
