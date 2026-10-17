#!/bin/sh
# Sourced by backup.sh and restore.sh (T5.3, docs/tasks/T5.3-backup-restore.md).
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PROJECT=${COMPOSE_PROJECT_NAME:-sandman}
FORMAT=1

die() { echo "$(basename "$0"): $*" >&2; exit 1; }
dc() { docker compose -p "$PROJECT" -f "$ROOT/deploy/docker-compose.yml" --profile '*' "$@"; }
sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
memgraph_image() { dc config --images | grep '^memgraph/memgraph'; }
