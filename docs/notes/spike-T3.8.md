# Spike T3.8: ArTEMiS runners and the new code extractor

Date: 2026-11-12. Pin: ArDoCo `21ef682` (`2.2.0-SNAPSHOT`).

## Scope

This spike answers U1 to U4 of T3.8 as far as the local machine allows.
No LLM key and no gateway were available. The three upstream runners were NOT run.
Nothing in this note is a measured LLM result.

## Result table

| Item | Question | Result | Method |
|------|----------|--------|--------|
| U1 | Do `Artemis` class and datafile strategies give usable links on a staged tree? | NOT measured | No LLM available |
| U2 | Can `ArtemisInExArch` use the gateway for both LLM roles? | Feasible by source reading. NOT run. | Source of `21ef682` |
| U3 | Are the Python and C++ extractors deterministic? | Not out of the box. Equal after canonicalization in SANDMAN. | `MixedLanguagesTest` |
| U4 | Does the ACM growth break the F-1 budget? | ACM grows by 40 to 90 percent. See table below. | `mvn -Pbenchmark` |

Links per approach, cost and time per approach are NOT measured.

## U2: source analysis

* `ArtemisInExArch` takes two `LargeLanguageModel` enum values: `llmForExArch` and `llmForNer`.
* `LlmArchitectureProviderAgent` takes the enum for the ExArch role.
* `ArtemisNer`, `ArtemisNerAgent` and `ArtemisNerInformant` take the enum for the NER role.
* No `ChatModel` injection point exists.
* The NER role can use a gateway stage like `ArtemisRunner` of the inconsistency agent.
* The ExArch role can use the existing ExArch bridge of `ArdocodeApproach`.
* `ArtemisInExArch` requires `codeConfiguration.metamodel() == null`.

## U3: determinism

* The file order that Magika returns depends on the random staging directory.
* `codeFiles` order and ids therefore differ between two calls in one JVM.
* The `Map.of` in `AllLanguagesExtractor` has an iteration order that can differ between JVMs.
* SANDMAN now sorts `codeFiles` by path and compilation unit id. It then reassigns the same id set in sorted order.
* With this step, two runs in one JVM give equal output (`MixedLanguagesTest`).
* NOT measured: equality across JVMs for Python and C++.

## Upstream quirk: duplicate codeFiles

* Upstream emits several `codeFiles` entries for one Java unit (for example `Greeter` three times).
* SANDMAN does not deduplicate them.
* This can affect datafile linking. Check it in step 6.

## Mapping notes

* Python units map to elements. Paths with spaces work.
* C++ yields assemblies without compilation units. SANDMAN emits only the file element.
* Build files (`Dockerfile`, `pom.xml`) get a file element and a warning.

## U4: ACM size

Old cached ACMs were written before `codeFiles` existed. New values come from `writesTheCodeModelsForTheTlrAgentTests` (benchmark profile).

| Project | Before (bytes) | After (bytes) | Files sent |
|---------|----------------|---------------|------------|
| TeaStore | 620 k | 978 605 | 558 |
| MediaStore | 261 k | 489 697 | 419 |
| TEAMMATES | 3.6 M | 4 562 594 | 2447 |
| BigBlueButton | 1.1 M | 2 024 577 | 3201 |
| JabRef | 7.2 M | 11 087 972 | 13 420 |

* The staged trees also changed (more files per project). The growth mixes both effects.
* NOT measured: the effect on the F-1 shared code model budget. Compare with the T5.4 limit.
* NOT measured: Magika time on the larger trees. TEAMMATES extraction took 40.9 s with 359 MiB peak heap.

## Live run on TeaStore (2026-11-12, `qwen3:4b`, `local-only`)

Setup: `sandman-example`, all `artemis*` approaches enabled temporarily, one full workflow.

| Item | Result |
|------|--------|
| Full workflow time | 1507 s with the ArTEMiS approaches. 126 to 162 s without them. |
| Links | `artemis` 46, `artemis-class` 6, `ardoco` 40, `transarc` 603, `stub-name-match` 3209. |
| `artemis-datafile`, `artemis-transarc`, `artemis-exarch` | 0 links. TeaStore has no data files. Not investigated for the other two. |
| Cost | Zero money cost (local model). Each LLM call takes 18 to 96 s. |
| SAD to SAM, `artemis` against gold (27 links) | Precision 0.913, recall 0.778, F1 0.84. |
| SAD to SAM, `ardoco` against the same gold | Precision 1.0, recall 0.741, F1 0.851. |
| Difference in F1 | -0.011 for `artemis`, 95 % CI [-0.12, 0.15], McNemar p = 1.0. Not significant. |
| SAD to code | Not comparable. The gold file lists package directories. The exports list class files. |

* U1 is not resolved. A 4B model is a weak test for the NER step. A stronger route may change the numbers.
* TeaStore equivalence against the standalone upstream runner with recorded LLM answers: NOT done.
* Production `CodeModelSource` for the class and datafile strategies: NOT wired.
* MediaStore 18 against 29 links: NOT re-checked.
