#!/usr/bin/env sh
# Generates deploy/secrets/* for a local SANDMAN deployment. Never overwrites existing secret files.
# Afterwards put real provider keys into secrets/gateway.env (OPENAI_API_KEY, OPENROUTER_API_KEY).
# Plugin services: add their names to PLUGINS (token pair per plugin, see ADR-0012); LLM-using plugins
# additionally need virtual keys in gateway/config.json and gateway.env (docs/plugins.md).
set -eu
cd "$(dirname "$0")"
mkdir -p secrets ../data/llm-records ../data/logs
umask 077

SERVICES="orchestrator extraction-agent tlr-agent inconsistency-agent query-service dashboard"
PLUGINS="plugin-stubs"
LLM_COMPONENTS="extraction tlr inconsistency query"   # get a cloud key and a local-only key

rand() { openssl rand -hex 24; }
write() { [ -e "secrets/$1" ] && echo "keep    secrets/$1" || { printf '%s' "$2" > "secrets/$1"; echo "created secrets/$1"; }; }
upper() { echo "$1" | tr '[:lower:]-' '[:upper:]_'; }

if [ ! -e secrets/memgraph_password ]; then
  MG_PW=$(rand)
  write memgraph_password "$MG_PW"
  write memgraph.env "MEMGRAPH_USER=sandman
MEMGRAPH_PASSWORD=$MG_PW
"
fi

if [ ! -e secrets/gateway.env ]; then
  ADMIN_PW="Sa1!$(rand)"                 # Bifrost policy: >= 12 chars, upper, lower, digit, special
  write gateway_admin_password "$ADMIN_PW"   # operator only; never mounted into agents
  ENV="OPENAI_API_KEY=
OPENROUTER_API_KEY=
BIFROST_ENCRYPTION_KEY=$(rand)
BIFROST_ADMIN_PASSWORD=$ADMIN_PW
"
  for c in orchestrator experiments $LLM_COMPONENTS; do
    VK="sk-bf-$(rand)"
    ENV="${ENV}VK_$(upper "$c")=$VK
"
    [ "$c" = experiments ] || write "llm_key_$c" "$VK"
  done
  for c in $LLM_COMPONENTS; do
    VK="sk-bf-$(rand)"
    ENV="${ENV}VK_$(upper "$c")_LOCAL=$VK
"
    write "llm_key_${c}_local" "$VK"
  done
  write gateway.env "$ENV"
fi

# Each service/plugin gets only its own outbound token; receivers verify against hashes.
for s in $SERVICES $PLUGINS; do write "svc_token_$s" "$(rand)"; done
for p in $PLUGINS; do write "plugin_in_$p" "$(rand)"; done   # owner -> plugin calls
VERIFY="{"
SEP=""
for s in $SERVICES $PLUGINS; do
  H=$(openssl dgst -sha256 < "secrets/svc_token_$s" | awk '{print $NF}')
  case " $PLUGINS " in *" $s "*) NAME="plugin:$s" ;; *) NAME="$s" ;; esac   # plugin callers: read-only, query-service only
  VERIFY="$VERIFY$SEP\"$NAME\": \"$H\""; SEP=", "
done
printf '%s}' "$VERIFY" > secrets/svc_tokens_verify.json   # regenerated on every run
echo "wrote   secrets/svc_tokens_verify.json"

write admin_bootstrap_password "$(rand)"
write session_secret "$(rand)"
write dashboard_admin_password "$(rand)"
write dashboard_session_secret "$(rand)"
