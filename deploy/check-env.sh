#!/bin/sh
# Fail-closed preflight for deploy/secrets/gateway.env (ADR-0004): self-hosted LLM needs https:// and a key.
set -eu
f="$(cd "$(dirname "$0")" && pwd)/secrets/gateway.env"
[ -f "$f" ] || { echo "check-env: $f missing; run 'make secrets'" >&2; exit 1; }
get() { sed -n "s/^$1=//p" "$f" | tail -n 1 | sed "s/^[\"']//; s/[\"']\$//"; }
url=$(get SELFHOSTED_LLM_URL)
[ -z "$url" ] && exit 0
case "$url" in
  https://*) ;;
  *) echo "check-env: SELFHOSTED_LLM_URL must start with https:// (prompts would leave this host in cleartext)" >&2; exit 1 ;;
esac
[ -n "$(get SELFHOSTED_LLM_KEY)" ] || { echo "check-env: SELFHOSTED_LLM_KEY must not be empty when SELFHOSTED_LLM_URL is set" >&2; exit 1; }
