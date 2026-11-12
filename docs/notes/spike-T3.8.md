# Spike T3.8: ArTEMiS runners and the new code extractor

Date: 2026-11-12. Pin: ArDoCo `21ef682` (`2.2.0-SNAPSHOT`).

## Scope

This spike answers U1 to U4 of T3.8 as far as the local machine allows.
No LLM key and no gateway were available. The three upstream runners were NOT run.
Nothing in this note is a measured LLM result.

## Result table

| Item | Question | Result | Method |
|------|----------|--------|--------|
| U1 | Do `Artemis` class and datafile strategies give usable links on a staged tree? | Partly. With `gpt-6-luna` on TeaStore: `artemis-class` 7 links, no data files in TeaStore. Class links do not match the package-level gold. | Live run, see eval-T3.9 |
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

Root cause (`CodeExtractor.extractCodeFiles`, `21ef682`): the method loops over `codeEndpoints` and adds one `CodeFile` for each unit of `codeEndpoint.getAllCompilationUnits()`. With `CODE_WITH_COMPILATION_UNITS_AND_PACKAGES` the endpoints hold every package, and `getAllCompilationUnits()` is recursive. A unit therefore appears once for each package above it, including the nested packages.

* Magika is NOT the cause. In the cached ACMs of MediaStore and TeaStore no path has both an entry with a unit and an entry without one.
* MediaStore: 1097 entries, 419 distinct paths. 777 entries with a unit (99 units, 96 of them 8 times, one 7 times, two once), 320 without a unit (Magika only, no duplicates).
* TeaStore: 1563 entries, 558 distinct paths. 1214 with a unit (209 units, 200 repeated), 349 without a unit.
* All repeated entries are `java`, have the same `compilationUnitId` and differ only in `id`. Entries for several types of one file do not exist (one unit per file).
* Impact: the `ElementMapping` of the extract skill reads the model content, not `codeFiles`, so elements are unaffected. The datafile strategy and the read-only tools of ArDoCo read `codeFiles` and see each Java file several times. The ACM carries about 2.6 times too many entries. Determinism is unaffected, because `canonicalizeCodeFiles` already sorts.
* Decision: deduplicate in `CodeService.canonicalizeCodeFiles` by path and `compilationUnitId`. It is safe: the entries are identical apart from `id`, and the ids are reassigned in sorted order afterwards. The agent card version is now `2.2.0-SNAPSHOT+21ef682.c4`. `MixedLanguagesTest` no longer uses `distinct()` and so covers the case on a mixed tree. `docs/plugins.md` and `skills.schema.json` say it.
* Not run: `mvn -Pbenchmark verify` (`CodeModelEquivalenceTest`). It canonicalizes both sides with the same method, so the dedupe is symmetric. Cached benchmark ACMs and the tlr-agent fixtures are unchanged and still hold the duplicates.

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

## Live run on TeaStore and MediaStore (2026-11-12, `openai/gpt-6-luna`, `cloud-allowed`)

Details and numbers: [eval-T3.9.md](eval-T3.9.md).

| Item | Result |
|------|--------|
| Full workflow time (TeaStore) | 564 s (`qwen3:4b`: 1507 s) |
| Links | `artemis` 58, `artemis-class` 7, `artemis-datafile` 0, `artemis-transarc` 777, `artemis-exarch` 552 |
| SAD to SAM, `artemis` (27 gold links) | Precision 0.931, recall 1.000, F1 0.964. `ardoco`: 0.851. |
| MediaStore, `artemis` (31 gold links) | F1 0.954. `ardoco`: 0.694. |
| `artemis-transarc`, `artemis-exarch` | 0 links with `qwen3:4b` was NOT caused by the model or the wiring. See eval-T3.9. |
| Code extractor | The new extractor (without repeated `codeFiles`) fed all runs: ACM of 115 files. |

* U1: the class strategy gives links (7) with a strong model, the datafile strategy has no test data in TeaStore. Usefulness against a class-level gold is NOT measured (the gold lists packages).
* MediaStore 18 against 29 links: this run gives 18 `ardoco` and 34 `artemis` links. Equivalence with the standalone upstream runner is NOT checked.
* TeaStore equivalence against the standalone upstream runner with recorded LLM answers: NOT done.
* The tlr approaches `artemis-class`, `artemis-transarc` and `artemis-exarch` read the code model through `CodeModelClient` and ran live. The old note "Production `CodeModelSource` NOT wired" was not re-checked for the inconsistency agent.
