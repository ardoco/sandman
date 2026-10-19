#!/usr/bin/env bash
# Builds ArDoCo (inconsistency branch, pinned) from source and installs it into a Maven repository (ADR-0015 B1).
# The branch hard-codes NAER 2.1.0-SNAPSHOT; it is switched to the published 2.1.0 in the scratch clone only.
# ARDOCO_M2: target Maven repository (default ~/.m2/repository). ARDOCO_WORK: scratch directory for the clone.
# A marker file in the target repository makes a second run with the same commit a no-op.
set -euo pipefail

ARDOCO_COMMIT=0d1f22b11caf3a758e5d32e5d3b06020c15d7407 # feature/make-inconsistency-great-again
M2=${ARDOCO_M2:-$HOME/.m2/repository}
WORK=${ARDOCO_WORK:-${TMPDIR:-/tmp}/ardoco-id}
MARKER=$M2/io/github/ardoco/id/pipeline-id/2.1.0-SNAPSHOT/.sandman-commit

if [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$ARDOCO_COMMIT" ]; then
  echo "ArDoCo $ARDOCO_COMMIT is already installed in $M2"
  exit 0
fi

if [ -z "${JAVA_HOME:-}" ] && [ -x /usr/libexec/java_home ]; then
  JAVA_HOME=$(/usr/libexec/java_home -v 21)
  export JAVA_HOME
fi

mkdir -p "$WORK" "$M2" && cd "$WORK"
[ -d ardoco ] || git clone -q https://github.com/ArDoCo/ardoco.git
git -C ardoco fetch -q origin "$ARDOCO_COMMIT" 2>/dev/null || git -C ardoco fetch -q --all --tags
git -C ardoco checkout -q -f "$ARDOCO_COMMIT"

# only the NAER dependency version (hard-coded in core/framework/common and tlr/stages-tlr/artemis)
for f in $(grep -rl --include=pom.xml '<artifactId>named-architecture-entity-recognition</artifactId>' ardoco); do
  sed -i.bak '/<artifactId>named-architecture-entity-recognition<\/artifactId>/,/<\/dependency>/ s#<version>2.1.0-SNAPSHOT</version>#<version>2.1.0</version>#' "$f"
  rm "$f.bak"
done
git -C ardoco diff --stat

(cd ardoco && mvn -B -Dmaven.repo.local="$M2" install -DskipTests -Dmaven.javadoc.skip=true)
echo "$ARDOCO_COMMIT" >"$MARKER"
echo "OK. ArDoCo $ARDOCO_COMMIT installed in $M2. Benchmark: $WORK/ardoco/core/tests-base/src/main/resources/benchmark"
