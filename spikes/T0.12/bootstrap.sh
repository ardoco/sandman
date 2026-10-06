#!/usr/bin/env bash
# Builds ArDoCo (inconsistency branch, pinned) into an isolated Maven repo outside the sandman checkout.
# NAER 2.1.0-SNAPSHOT is not published and depends on ardoco core:common 2.1.0-SNAPSHOT (cycle), so it is bootstrapped in two passes.
set -euo pipefail

WORK=${WORK:-/private/tmp/claude-501/ardoco-spike}
ARDOCO_COMMIT=0d1f22b11caf3a758e5d32e5d3b06020c15d7407 # feature/make-inconsistency-great-again
NAER_COMMIT=d631a7085b0c97909e1edcf03f651048e8718170   # named-architecture-entity-recognition main
export JAVA_HOME=${JAVA_HOME:-$(/usr/libexec/java_home -v 21)}
MVN="mvn -B -Dmaven.repo.local=$WORK/m2"

mkdir -p "$WORK" && cd "$WORK"
[ -d ardoco ] || git clone -q https://github.com/ArDoCo/ardoco.git
[ -d naer ] || git clone -q https://github.com/ArDoCo/named-architecture-entity-recognition.git naer
git -C ardoco checkout -q "$ARDOCO_COMMIT"
git -C naer checkout -q "$NAER_COMMIT"

(cd ardoco && $MVN -q -N install)                  # parent pom of the branch
(cd naer && $MVN -q install -DskipTests)           # pass 1: against published core:common snapshot
(cd ardoco && time $MVN install -DskipTests)       # full branch build
(cd naer && $MVN -q -o install -DskipTests)        # pass 2: against the branch's core:common
(cd ardoco && $MVN -q -o install -DskipTests)

echo "OK. Benchmark: $WORK/ardoco/core/tests-base/src/main/resources/benchmark"
