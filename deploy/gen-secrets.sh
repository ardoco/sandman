#!/usr/bin/env sh
# Generates deploy/secrets/* for a local SANDMAN deployment. Never overwrites existing secret files.
# Afterwards put real provider keys into secrets/gateway.env (OPENAI_API_KEY, OPENROUTER_API_KEY)
# For the local-only routes the default is the compose container (SELFHOSTED_LLM_URL=http://ollama:11434, profile local-llm, 'make models').
# Remote instead: SELFHOSTED_LLM_URL=https://<open-webui-host>/ollama and SELFHOSTED_LLM_KEY=<Open WebUI API key>.
# Plugin services: add their names to PLUGINS (token pair per plugin, see ADR-0012); LLM-using plugins
# additionally need virtual keys in gateway/config.json and gateway.env (docs/plugins.md).
set -eu
cd "$(dirname "$0")"
mkdir -p secrets ../data/llm-records ../data/logs
GID="${SANDMAN_LOG_GID:-10001}"   # containers get this group via group_add and all append to one JSONL file
if [ -L ../data/logs ] || [ -L ../data/logs/sandman.jsonl ]; then echo "refusing symlinked data/logs" >&2; exit 1; fi
touch ../data/logs/sandman.jsonl
fix_log_modes() { chgrp "$GID" "$1/logs" "$1/logs/sandman.jsonl" && chmod 2770 "$1/logs" && chmod 660 "$1/logs/sandman.jsonl"; }
if ! fix_log_modes ../data 2>/dev/null; then   # host user is not in the group: do it as root in a throwaway container
  docker compose -f docker-compose.yml run --rm --no-deps -u 0 -e GID="$GID" -v "$(cd ../data/logs && pwd)":/d --entrypoint sh edge-proxy -c \
    '[ ! -L /d ] && [ ! -L /d/sandman.jsonl ] && chgrp "$GID" /d /d/sandman.jsonl && chmod 2770 /d && chmod 660 /d/sandman.jsonl'
fi
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
SELFHOSTED_LLM_URL=http://ollama:11434
SELFHOSTED_LLM_KEY=
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
write e2e_admin_password "$(rand)"
write session_secret "$(rand)"
write dashboard_admin_password "$(rand)"
write dashboard_session_secret "$(rand)"

# Compose file secrets are bind mounts that keep host owner/mode (uid/gid/mode are ignored outside swarm). Containers run as
# uid 10001 and get $GID via group_add, so the files must be group-readable: 0640, dir 2750, group $GID, never world-readable.
# Host user not in the group (CI runner, macOS): chgrp as root in a throwaway container, like data/logs above.
FIX_SECRETS='[ ! -L "$D" ] && ! find "$D" -type l | grep -q . && chgrp "$GID" "$D" && find "$D" -type f -exec chgrp "$GID" {} + && chmod 2750 "$D" && find "$D" -type f -exec chmod 640 {} +'
if ! D=secrets GID="$GID" sh -c "$FIX_SECRETS" 2>/dev/null; then
  docker compose -f docker-compose.yml run --rm --no-deps -u 0 -e GID="$GID" -e D=/d -e FIX_SECRETS="$FIX_SECRETS" -v "$(pwd)/secrets":/d --entrypoint sh edge-proxy -c \
    'sh -c "$FIX_SECRETS"' || { echo "could not make secrets group-readable (symlink in secrets/ or no docker)" >&2; exit 1; }
fi
echo "secrets group $GID, mode 0640"
