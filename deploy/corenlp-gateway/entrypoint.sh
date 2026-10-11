#!/bin/sh
# Reads the credentials file of the active mode (one line user:password), derives the Basic token, hashes the caller
# tokens for basic_auth and starts Caddy.
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
for caller in tlr-agent inconsistency-agent; do
  token=
  IFS= read -r token < "/run/secrets/corenlp_client_token_$caller" || [ -n "$token" ]
  [ "${#token}" -ge 32 ] || { echo "corenlp-gateway: corenlp_client_token_$caller must hold a token of at least 32 characters" >&2; exit 1; }
  var=CORENLP_HASH_$(printf '%s' "$caller" | tr 'a-z-' 'A-Z_')
  export "$var=$(caddy hash-password --plaintext "$token")"
done
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
