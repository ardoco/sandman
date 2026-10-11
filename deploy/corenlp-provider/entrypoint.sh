#!/bin/sh
# Reads corenlp_local_credentials (one line user:password) into USERNAME and PASSWORD of the provider service.
set -eu
line=
IFS= read -r line < /run/secrets/corenlp_local_credentials || [ -n "$line" ]
case "$line" in
  ?*:?*) ;;
  *) echo "corenlp-provider: corenlp_local_credentials must hold one line user:password" >&2; exit 1 ;;
esac
USERNAME="${line%%:*}"
PASSWORD="${line#*:}"
export USERNAME PASSWORD
exec java -jar /usr/src/scnlp/stanfordnlp.jar
