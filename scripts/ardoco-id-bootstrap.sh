#!/usr/bin/env bash
# Builds ArDoCo (merge commit of the inconsistency branch, pinned) from source and installs it into a Maven repository (ADR-0015 B1).
# ARDOCO_M2: target Maven repository (default ~/.m2/repository). ARDOCO_WORK: scratch directory for the clone.
# A marker file in the target repository makes a second run with the same commit a no-op.
set -euo pipefail

ARDOCO_COMMIT=21ef6821e1b8fe378ee58ae075c1c05a14fad436 # merge of PR #186 (make-inconsistency-great-again) on main
M2=${ARDOCO_M2:-$HOME/.m2/repository}
WORK=${ARDOCO_WORK:-${TMPDIR:-/tmp}/ardoco-id}
MARKER=$M2/io/github/ardoco/id/pipeline-id/2.2.0-SNAPSHOT/.sandman-commit

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

(cd ardoco && mvn -B -Dmaven.repo.local="$M2" install -DskipTests -Dmaven.javadoc.skip=true)
echo "$ARDOCO_COMMIT" >"$MARKER"
echo "OK. ArDoCo $ARDOCO_COMMIT installed in $M2. Benchmark: $WORK/ardoco/core/tests-base/src/main/resources/benchmark"
