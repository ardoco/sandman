#!/bin/sh
# Reads the credentials file of the active mode (one line user:password), derives the Basic token and starts Caddy.
# The credentials stay in this process; they are not in the compose file, the image or the container configuration.
set -eu
case "${SANDMAN_CORENLP_MODE:-remote}" in
  remote) file=/run/secrets/corenlp_credentials; CORENLP_UPSTREAM="${SANDMAN_CORENLP_URL:-https://vdl-ws.sdq.kastel.kit.edu}" ;;
  local) file=/run/secrets/corenlp_local_credentials; CORENLP_UPSTREAM=http://corenlp-provider:8080 ;;
  *) echo "corenlp-gateway: SANDMAN_CORENLP_MODE must be remote or local" >&2; exit 1 ;;
esac
line=
IFS= read -r line < "$file" || [ -n "$line" ]
line="${line%"$(printf '\r')"}"
case "$line" in
  ?*:?*) ;;
  *) echo "corenlp-gateway: $file must hold one line user:password" >&2; exit 1 ;;
esac
CORENLP_BASIC=$(printf '%s' "$line" | base64 | tr -d '\n')
export CORENLP_UPSTREAM CORENLP_BASIC
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
