#!/bin/sh
# Fail-closed preflight for deploy/secrets/gateway.env (ADR-0004) and the CoreNLP settings (T2.5): a self-hosted LLM needs https:// and a key,
# except the in-compose Ollama container (http://ollama:11434, profile local-llm), which never leaves the host.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
otel() { v=$(printenv "$1" || true); [ -n "$v" ] || v=$(sed -n "s/^$1=//p" "$here/.env" 2>/dev/null | tail -n 1); echo "$v"; }
if [ "$(otel OTEL_SDK_DISABLED)" = false ] && [ -z "$(otel OTEL_EXPORTER_OTLP_ENDPOINT)" ]; then
  echo "check-env: OTEL_SDK_DISABLED=false needs OTEL_EXPORTER_OTLP_ENDPOINT (e.g. http://otel-lgtm:4318)" >&2; exit 1
fi
mode=$(otel SANDMAN_CORENLP_MODE); mode=${mode:-remote}
case "$mode" in
  local) ;;
  remote)
    cf="$here/secrets/corenlp_credentials"
    [ -f "$cf" ] || { echo "check-env: $cf missing; run 'make secrets' and put user:password of the CoreNLP service into it" >&2; exit 1; }
    cred=$(head -n 1 "$cf" | tr -d '\r')
    case "$cred" in
      ?*:?*) ;;
      *) echo "check-env: $cf must hold one line user:password (or set SANDMAN_CORENLP_MODE=local)" >&2; exit 1 ;;
    esac
    case "$cred" in
      *change-me*) echo "check-env: $cf still holds the placeholder; put the user:password of the CoreNLP service into it (or set SANDMAN_CORENLP_MODE=local)" >&2; exit 1 ;;
    esac
    curl_url=$(otel SANDMAN_CORENLP_URL)
    case "${curl_url:-https://vdl-ws.sdq.kastel.kit.edu}" in
      https://*|http://corenlp-provider:8080) ;;
      *) echo "check-env: SANDMAN_CORENLP_URL must start with https:// (credentials would leave this host in cleartext), or be exactly http://corenlp-provider:8080" >&2; exit 1 ;;
    esac
    ;;
  *) echo "check-env: SANDMAN_CORENLP_MODE must be remote or local, got '$mode'" >&2; exit 1 ;;
esac
f="$(cd "$(dirname "$0")" && pwd)/secrets/gateway.env"
[ -f "$f" ] || { echo "check-env: $f missing; run 'make secrets'" >&2; exit 1; }
get() { sed -n "s/^$1=//p" "$f" | tail -n 1 | sed "s/^[\"']//; s/[\"']\$//"; }
url=$(get SELFHOSTED_LLM_URL)
[ -z "$url" ] && exit 0
[ "$url" = "http://ollama:11434" ] && exit 0
case "$url" in
  https://*) ;;
  *) echo "check-env: SELFHOSTED_LLM_URL must start with https:// (prompts would leave this host in cleartext), or be exactly http://ollama:11434" >&2; exit 1 ;;
esac
[ -n "$(get SELFHOSTED_LLM_KEY)" ] || { echo "check-env: SELFHOSTED_LLM_KEY must not be empty when SELFHOSTED_LLM_URL is set" >&2; exit 1; }
