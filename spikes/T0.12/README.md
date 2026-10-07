# Spike T0.12 – ArDoCo inconsistency detection

Throwaway code. Findings: `docs/notes/spike-T0.12.md`.

`bootstrap.sh` patches the NAER version in two poms of the scratch clone only (`core/framework/common`, `tlr/stages-tlr/artemis`); the ArDoCo
commit itself is unchanged. The spike's `pom.xml` is unchanged (`ardoco.version` = the branch's `2.1.0-SNAPSHOT`, installed by the bootstrap).

Requires JDK 21 (JDK 26 also builds), Maven 3.9, network for the first build. Everything lands in `$WORK`
(default `/private/tmp/claude-501/ardoco-naer210`), not in `~/.m2`.

```sh
./bootstrap.sh                       # clone ArDoCo @0d1f22b, NAER 2.1.0-SNAPSHOT -> 2.1.0 (Maven Central), one-pass build (~1 min, ~750 MB deps)

WORK=/private/tmp/claude-501/ardoco-naer210
B=$WORK/ardoco/core/tests-base/src/main/resources/benchmark
run() { mvn -B -q -Dmaven.repo.local=$WORK/m2 compile exec:java -Dexec.args="$*"; }

# 1. InconsistencyDetection on MediaStore (internal links), then the same pipeline with links replaced by
#    (a) ArDoCo's own links reduced to sentence level and (b) the gold standard, each with two RI mappings
run detect $B/mediastore/text_2016/mediastore.txt $B/mediastore/model_2016/pcm/ms.repository $WORK/out-ms \
    $B/mediastore/goldstandards/goldstandard_sad_2016-sam_2016.csv
run detect $B/mediastore/text_2016/mediastore.txt $B/mediastore/model_2016/uml/ms.uml $WORK/out-ms-uml

# 2. CoreNLP sentence splitting + sentence-key hashes for all benchmark texts and a Markdown fixture
run sentences fixtures/wrapped.md $B/*/text_*/*.txt

# 3. Pre-split text (one sentence per line, ssplit.eolonly) injected as PreprocessingData
run presplit $B/mediastore/text_2016/mediastore.txt $B/mediastore/model_2016/pcm/ms.repository $WORK/out-ms-presplit
run presplit fixtures/wrapped.sentences.txt $B/mediastore/model_2016/pcm/ms.repository $WORK/out-wrapped-presplit
```

`Spike.java` starts with a self-check of the sentence-key hash against `contracts/conformance/vectors.json`.
