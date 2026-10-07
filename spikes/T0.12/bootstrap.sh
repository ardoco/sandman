#!/usr/bin/env bash
# Builds ArDoCo (inconsistency branch, pinned) in one pass into an isolated Maven repo outside the sandman checkout.
# The branch hard-codes NAER 2.1.0-SNAPSHOT; it is switched to the published 2.1.0 in the scratch clone only.
set -euo pipefail

WORK=${WORK:-/private/tmp/claude-501/ardoco-naer210}
ARDOCO_COMMIT=0d1f22b11caf3a758e5d32e5d3b06020c15d7407 # feature/make-inconsistency-great-again
export JAVA_HOME=${JAVA_HOME:-$(/usr/libexec/java_home -v 21)}
MVN="mvn -B -Dmaven.repo.local=$WORK/m2"

mkdir -p "$WORK" && cd "$WORK"
[ -d ardoco ] || git clone -q https://github.com/ArDoCo/ardoco.git
git -C ardoco checkout -q -f "$ARDOCO_COMMIT"

# only the NAER dependency version (hard-coded in core/framework/common and tlr/stages-tlr/artemis)
for f in $(grep -rl --include=pom.xml '<artifactId>named-architecture-entity-recognition</artifactId>' ardoco); do
  sed -i.bak '/<artifactId>named-architecture-entity-recognition<\/artifactId>/,/<\/dependency>/ s#<version>2.1.0-SNAPSHOT</version>#<version>2.1.0</version>#' "$f"
  rm "$f.bak"
done
git -C ardoco diff --stat

(cd ardoco && time $MVN install -DskipTests)

echo "OK. Benchmark: $WORK/ardoco/core/tests-base/src/main/resources/benchmark"
