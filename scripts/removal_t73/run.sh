#!/bin/sh
# TLR command of `sandman eval removal --cmd`. Needs Docker (CoreNLP) and a JDK 21.
set -eu
root=$(cd "$(dirname "$0")/../.." && pwd)
jobs=$(cd "$1" && pwd)
cp_file="$root/.work/cp.txt"
if [ ! -s "$cp_file" ]; then
  (cd "$root" && mvn -o -q -pl services/tlr-agent/approach-ardoco -am test-compile dependency:build-classpath \
    -Dmdep.outputFile="$cp_file" -Dmdep.includeScope=test -DskipTests)
fi
module="$root/services/tlr-agent/approach-ardoco"
cd "$module"
exec java -cp "$module/target/classes:$module/target/test-classes:$(cat "$cp_file")" \
  "$root/scripts/removal_t73/RemovalDriver.java" "$jobs"
