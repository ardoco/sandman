#!/bin/sh
# Restore of a backup made by scripts/backup.sh into the volumes of the compose project (default sandman).
# Usage: scripts/restore.sh ARCHIVE [--force] [--with-secrets]
set -eu
. "$(dirname "$0")/backup-common.sh"

archive= force=0 with_secrets=0
for a in "$@"; do
  case $a in
    --force) force=1 ;;
    --with-secrets) with_secrets=1 ;;
    -*) die "usage: restore.sh ARCHIVE [--force] [--with-secrets]" ;;
    *) [ -z "$archive" ] || die "only one ARCHIVE is allowed"; archive=$a ;;
  esac
done
[ -f "$archive" ] || die "usage: restore.sh ARCHIVE [--force] [--with-secrets]"
[ -f "$archive.sha256" ] || die "checksum file $archive.sha256 is missing"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

(cd "$(dirname "$archive")" && sha256 -c "$(basename "$archive").sha256" >/dev/null) || die "checksum mismatch: archive is damaged"
tar -xzf "$archive" -C "$work"

field() { sed -n "s/^  \"$1\": \"\{0,1\}\([^\",]*\)\"\{0,1\},\{0,1\}\$/\1/p" "$work/manifest.json"; }
[ "$(field formatVersion)" = "$FORMAT" ] || die "archive format $(field formatVersion) is not supported (need $FORMAT)"
[ "$(field memgraphImage)" = "$(memgraph_image)" ] || die "Memgraph image mismatch: archive $(field memgraphImage), stack $(memgraph_image)"
latest=$(grep -o 'v\.version = [0-9]*' "$ROOT/contracts/kg/init.cypher" | sed 's/.*= //' | sort -n | tail -n 1)
[ "$(field kgSchemaVersion)" -le "$latest" ] || die "KG schema $(field kgSchemaVersion) is newer than this repository ($latest)"
for pair in orchestrator.db:orchestrator users.db:query-service dashboard.db:dashboard; do
  have=$(sed -n "s/^    \"${pair%%:*}\": \([0-9]*\),\{0,1\}\$/\1/p" "$work/manifest.json")
  [ -z "$have" ] || [ "$have" -le "$(ls "$ROOT"/services/"${pair##*:}"/src/*/migrations/*.sql | wc -l)" ] ||
    die "${pair%%:*} schema version $have is newer than this repository"
done
[ "$with_secrets" = 0 ] || [ -f "$work/secrets.tar" ] || die "the archive holds no secrets"
[ -f "$work/secrets.tar" ] && [ "$with_secrets" = 0 ] && echo "note: the archive holds secrets; use --with-secrets to restore them" >&2

in_volume() { dc run --rm --no-deps -T -u root --entrypoint sh "$1" -c "$2"; }
volumes="memgraph:/var/lib/memgraph orchestrator:/data query-service:/data extraction-agent:/artifacts"
[ ! -f "$work/sqlite-dashboard.tar" ] || volumes="$volumes dashboard:/data"

if [ "$force" = 0 ]; then
  [ -z "$(dc ps -q --status running)" ] || die "the stack is running; stop it or use --force"
  for v in $volumes; do
    n=$(in_volume "${v%%:*}" "ls -A ${v##*:} | wc -l") || die "cannot inspect ${v##*:} of ${v%%:*}"
    [ "$n" -eq 0 ] || die "volume ${v##*:} of ${v%%:*} is not empty; use --force to overwrite it"
  done
else
  dc down
fi

put() { in_volume "$1" "find $2 -mindepth 1 -delete && tar -C $2 -xpf -" < "$3"; }
put memgraph /var/lib/memgraph "$work/memgraph.tar"
put orchestrator /data "$work/sqlite-orchestrator.tar"
put query-service /data "$work/sqlite-query-service.tar"
[ ! -f "$work/sqlite-dashboard.tar" ] || put dashboard /data "$work/sqlite-dashboard.tar"
put extraction-agent /artifacts "$work/artifact-store.tar"

if [ "$with_secrets" = 1 ]; then
  [ "$force" = 1 ] || [ ! -e "$ROOT/deploy/secrets" ] || die "deploy/secrets exists; use --force to overwrite it"
  echo "WARNING: restoring deploy/secrets from the archive." >&2
  tar -C "$ROOT/deploy" -xpf "$work/secrets.tar"
fi
echo "restore complete; start the stack with: make up"
