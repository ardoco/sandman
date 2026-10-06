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
* Sentence mapping: (S1) by ArDoCo sentence number; (S2) by normalized sentence text using the content-based TextUnit keys (ADR-0005 amendment).
* Build: (B1) pinned commit of the branch built in the Docker image and via `make ardoco`; (B2) wait for the release.

## Decision Outcome

Proposed: **L2 + S2 + B1**.

* **L2 (Q-20, answered 2026-10-06):** the ArDoCo detector adapter feeds the active trace links of all approaches from the KG into ArDoCo's inconsistency detection. A detector parameter `linkApproaches` (default: all) restricts the source; the equivalence test (T3.3) uses `[ardoco]`.
* **S2:** ArDoCo sentences are mapped to `TextUnit` sentence elements of the same document version by the hash of the normalized sentence text (the hash part of the sentence key, vectors `sentenceKey`); identical sentences are disambiguated by document order (`sentenceIndex`). ArDoCo's text alone does not determine the section part of the key, so the adapter never recomputes full keys. Model elements map by XMI/PCM id (`ModelElement.localKey`).
* **B1:** pinned commit until the release; then switch to Maven Central coordinates (T3.5).

Open points (reason for `proposed`):
* Whether ArDoCo's inconsistency pipeline accepts externally provided links (V-27). If not, the adapter needs an extension of the branch (contribution upstream); until then only the `ardoco` approach's links can be used, computed internally.
* Which doc/model pairs run and which canonical text rendering ArDoCo sees (R-025, open in the review).

### Consequences

* Good, because detection results improve with any better TLR approach without adapter changes.
* Good, because sentence mapping does not depend on matching CoreNLP's sentence numbering.
* Bad, because results with non-ArDoCo links are not comparable to published ArDoCo numbers (equivalence only with `linkApproaches: [ardoco]`).
* Bad, because differences in sentence splitting between the extraction agent and ArDoCo leave unmapped sentences (measured as alignment rate in T1.1/T0.12).

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
* Bad, because identical sentences in one section rely on the occurrence counter.

### B1 pinned commit / B2 wait

* B1 good, because work can start now; bad, because a longer image build.
* B2 good, because simple; bad, because blocks M3.

## More Information

* Related: ADR-0001, ADR-0005, ADR-0007, ADR-0012.
