---
status: proposed
date: 2026-10-06
---

# ArDoCo integration

## Context and Problem Statement

ArDoCo provides TLR (SAD↔SAM, SAD↔code via TransArC) and, on branch `feature/make-inconsistency-great-again`, inconsistency detection. Both run in-process in the Java agents (ADR-0001, ADR-0012). The branch is not released yet (Q-15). ArDoCo works on its own text and model representations (CoreNLP sentences, model ids), while SANDMAN stores elements with stable keys and trace links from several approaches in the KG. How is ArDoCo built, which inputs does it get, and how are its results mapped to the KG?

## Decision Drivers

* Inconsistency detection uses TLR information (Q-18), and links from all approaches should be usable (Q-20).
* Results must map exactly to KG elements (sentences, model elements) so fingerprints stay stable (ADR-0007).
* Equivalence with the standalone ArDoCo runner must remain testable.
* Reproducible builds until the branch is released.

## Considered Options

* Link source for detection: (L1) only links produced by the `ardoco` approach; (L2) active links of all approaches from the KG, configurable per detector.
* Sentence mapping: (S1) by ArDoCo's own sentence number; (S2) by normalized sentence text using the content-based TextUnit keys (ADR-0005 amendment); (S3) the adapter hands ArDoCo its own one-sentence-per-line rendering and maps sentence *i* to line *i*.
* Build: (B1) pinned commits built in the Docker image and via `make ardoco`; (B2) wait for the release.

## Decision Outcome

Proposed: **L2 + S3 + B1** (S3 replaces S2 after spike T0.12, `docs/notes/spike-T0.12.md`).

* **L2 (Q-20, answered 2026-10-06):** the ArDoCo detector adapter feeds the active trace links of all approaches from the KG into ArDoCo's inconsistency detection. A detector parameter `linkApproaches` (default: all) restricts the source; the equivalence test (T3.3) uses `[ardoco]`. ArDoCo has no input for external links (V-27): the adapter replaces ArDoCo's links with a custom pipeline step after `ConnectionGenerator` and maps sentence-level links to ArDoCo's text candidates heuristically (reproduces ArDoCo's 42 findings on MediaStore when fed its own links). Exact semantics need an upstream extension: an optional input with sentence-level links read by both detectors.
* **S3:** CoreNLP in ArDoCo does not split at line breaks (Markdown headings merge with the next sentence) and its settings are hard-coded. The adapter therefore renders the document's `TextUnit` sentences one per line, annotates them with the same CoreNLP properties plus `ssplit.eolonly` and hands them in as `PreprocessingData`; ArDoCo then skips its own NLP. ArDoCo sentence *i* (1-based in findings) = rendered line *i* = a known `TextUnit`; the sentence-text hash is only an assertion. Verified byte-identical output to the standard runner on MediaStore. This rendering is the canonical text ArDoCo sees (R-025). Model elements map by XMI/PCM id (`ModelElement.localKey`); the model extractor must also produce interfaces. Text findings are deduplicated by (sentence, word).
* **B1:** one pinned ArDoCo commit (`0d1f22b…`) built from source with `spikes/T0.12/bootstrap.sh`; `named-architecture-entity-recognition` comes as release 2.1.0 from Maven Central (published 2026-10-07, cycle gone). Until the branch switches upstream, the script patches the NAER version `2.1.0-SNAPSHOT` → `2.1.0` in two poms (`core/framework/common`, `tlr/stages-tlr/artemis`). Then Maven Central coordinates once the branch is released (T3.5). Adds about 570 MB to the image (CoreNLP models, onnxruntime).
* **LLM variant (V-29):** `ArtemisInconsistencyDetection` uses a non-streaming LangChain4j `ChatModel` configured only by process-wide environment variables or a `.env` file; per-project keys (`local-only` policy) need an upstream change that accepts a `ChatModel`. Until then the Artemis variant runs only for `cloud-allowed` projects with one deployment-wide configuration, and no `.env` may exist in the agent's working directory.

Open points (reason for `proposed`):
* Upstream changes in ArDoCo: switch the branch's NAER dependency to 2.1.0 (two poms); input for sentence-level links; `ChatModel` injection for the Artemis variant.
* ArDoCo on the Quarkus classpath (protobuf 3→4, JNA downgrade) is not verified (T3.3).
* Which doc/model pairs run (R-025, rest open in the review).

### Consequences

* Good, because detection results improve with any better TLR approach without adapter changes.
* Good, because sentence mapping is exact by construction and does not depend on CoreNLP's splitting.
* Bad, because results with non-ArDoCo links are not comparable to published ArDoCo numbers (equivalence only with `linkApproaches: [ardoco]`).
* Bad, because the adapter must build ArDoCo's pipeline itself (public factories; `definePipeline` is private) and keep CoreNLP annotation settings in sync with ArDoCo's.

### Confirmation

T0.12 spike answers V-16, V-17 and V-27; T3.3 equivalence test with `linkApproaches: [ardoco]`; alignment rate reported per benchmark project.

## Pros and Cons of the Options

### L1 ArDoCo links only

* Good, because identical to the standalone runner.
* Bad, because ignores better links from other approaches (contradicts Q-20).

### L2 all approaches' links

* Good, because uses SANDMAN's combined knowledge.
* Bad, because depends on ArDoCo accepting external links (V-27).

### S1 sentence numbers

* Good, because no text processing.
* Bad, because breaks whenever splitting or text changes.

### S2 sentence keys

* Good, because stable and identical to the KG keys.
* Bad, because ArDoCo's sentences differ from ours (no split at line breaks), and identical sentences occur in 5 of 10 benchmark texts.

### S3 adapter-controlled rendering

* Good, because exact by construction (verified on MediaStore) and independent of CoreNLP's splitting.
* Bad, because relies on ArDoCo's `PreprocessingData` input staying available.

### B1 pinned commit / B2 wait

* B1 good, because work can start now; bad, because a longer image build.
* B2 good, because simple; bad, because blocks M3.

## More Information

* Related: ADR-0001, ADR-0005, ADR-0007, ADR-0012.

## Amendments

* 2026-10-10, review item R-022 (task T2.1): TLR (`ardoco` approach, `approach-ardoco`) uses the released `io.github.ardoco.tlr:*:2.1.0` from Maven Central. Its SWATTR stages are identical to the pinned commit `0d1f22b`. B1 (pinned commit build) applies to the inconsistency branch only (T3.1) until T3.5. The approach declares `scopeMode: whole-project`.
