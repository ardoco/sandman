#!/bin/sh
# Backup of a running SANDMAN stack: Memgraph snapshot, SQLite online backups, artifact store.
# Usage: scripts/backup.sh [--out DIR] [--keep N] [--include-secrets]
set -eu
. "$(dirname "$0")/backup-common.sh"

out=$ROOT/backups keep=${BACKUP_KEEP:-7} secrets=0
while [ $# -gt 0 ]; do
  case $1 in
    --out) [ $# -ge 2 ] || die "--out needs a directory"; out=$2; shift 2 ;;
    --keep) [ $# -ge 2 ] || die "--keep needs a number"; keep=$2; shift 2 ;;
    --include-secrets) secrets=1; shift ;;
    *) die "usage: backup.sh [--out DIR] [--keep N] [--include-secrets]" ;;
  esac
done
case $keep in ''|*[!0-9]*) die "--keep needs a number" ;; esac

umask 077
mkdir -p "$out"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

for s in memgraph orchestrator query-service; do
  [ -n "$(dc ps -q --status running "$s")" ] || die "service $s is not running; start the stack first"
done

PYBK='import glob, os, sqlite3
os.makedirs("/tmp/bk", exist_ok=True)
for p in sorted(glob.glob("/data/*.db")):
    src = sqlite3.connect(p)
    dst = sqlite3.connect("/tmp/bk/" + os.path.basename(p))
    src.backup(dst)
    print("%s %d" % (os.path.basename(p), dst.execute("PRAGMA user_version").fetchone()[0]))
    dst.close()
    src.close()'
sqlite_backup() {
  dc exec -T "$1" python -c "$PYBK" >> "$work/versions"
  dc exec -T "$1" sh -c 'tar -C /tmp/bk -cf - . ; s=$?; rm -rf /tmp/bk; exit $s' > "$work/sqlite-$1.tar"
}

: > "$work/versions"
dc exec -T query-service tar -C /artifacts -cf - . > "$work/artifact-store.tar"
sqlite_backup orchestrator
sqlite_backup query-service
[ -z "$(dc ps -q --status running dashboard)" ] || sqlite_backup dashboard

mg='mgconsole --username="$MEMGRAPH_USER" --password="$MEMGRAPH_PASSWORD"'
dc exec -T memgraph sh -c "echo 'CREATE SNAPSHOT;' | $mg >/dev/null"
dc exec -T memgraph sh -c 'cd /var/lib/memgraph && f=$(ls snapshots | sort | tail -n 1) && tar --no-recursion -cf - snapshots "snapshots/$f"' > "$work/memgraph.tar"
kgv=$(dc exec -T memgraph sh -c "echo \"MATCH (v:SchemaVersion {id: 'kg'}) RETURN v.version;\" | $mg --output-format=csv" | tail -n 1 | tr -d '\r"')
case $kgv in ''|*[!0-9]*) die "cannot read the KG SchemaVersion" ;; esac

[ "$secrets" = 0 ] || {
  echo "WARNING: the archive will contain deploy/secrets. Store it encrypted and never share it." >&2
  tar -C "$ROOT/deploy" -cf "$work/secrets.tar" secrets
}

stamp=$(date -u +%Y%m%dT%H%M%SZ)
{
  echo '{'
  echo "  \"formatVersion\": $FORMAT,"
  echo "  \"createdAt\": \"$stamp\","
  echo "  \"project\": \"$PROJECT\","
  echo "  \"gitCommit\": \"$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)\","
  echo "  \"kgSchemaVersion\": $kgv,"
  echo "  \"memgraphImage\": \"$(memgraph_image)\","
  echo "  \"secretsIncluded\": $([ "$secrets" = 1 ] && echo true || echo false),"
  echo '  "sqliteUserVersion": {'
  sed 's/^\(.*\) \([0-9]*\)$/    "\1": \2,/' "$work/versions" | sed '$ s/,$//'
  echo '  },'
  echo '  "images": ['
  dc config --images | sort -u | sed 's/.*/    "&",/' | sed '$ s/,$//'
  echo '  ]'
  echo '}'
} > "$work/manifest.json"
rm "$work/versions"

name=$PROJECT-backup-$stamp.tar.gz
(cd "$work" && tar -czf "$out/$name" ./*)
(cd "$out" && sha256 "$name" > "$name.sha256")

if [ "$keep" -gt 0 ]; then
  ls -1t "$out/$PROJECT"-backup-*.tar.gz | tail -n +$((keep + 1)) | while read -r old; do rm -f "$old" "$old.sha256"; done
fi
echo "backup written: $out/$name"
