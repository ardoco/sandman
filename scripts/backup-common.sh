#!/bin/sh
# Sourced by backup.sh and restore.sh (T5.3, docs/tasks/T5.3-backup-restore.md).
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PROJECT=${COMPOSE_PROJECT_NAME:-sandman}
FORMAT=1

die() { echo "$(basename "$0"): $*" >&2; exit 1; }
dc() { docker compose -p "$PROJECT" -f "$ROOT/deploy/docker-compose.yml" --profile '*' "$@"; }
sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
traverse_image() { dc config --images | grep '^truespar/traverse'; }
kg_version() {
  dc exec -T query-service python -c "import os
from neo4j import GraphDatabase
with open(os.environ['SANDMAN_KG_PASSWORD_FILE']) as f:
    pw = f.read().strip()
with GraphDatabase.driver(os.environ['SANDMAN_KG_URI'], auth=(os.environ['SANDMAN_KG_USER'], pw)) as d:
    print(d.execute_query(\"MATCH (v:SchemaVersion {id: 'kg'}) RETURN v.version AS v\").records[0]['v'])"
}
