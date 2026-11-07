#!/bin/sh
cd "$(dirname "$0")" || exit 1
if grep -rEi "lang[c]hain|(^|[^a-z])r[i]g(-core)?( |=|\"|$)|llama[i]ndex" Cargo.toml sandman-agent/Cargo.toml sandman-agent/src; then
  echo "guard: agent framework dependency found" >&2
  exit 1
fi
