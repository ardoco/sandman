# Spike T2.7a - TransArC and ExArch on ArDoCo 2.1.0

Checked on 2026-10-18 against the released ArDoCo 2.1.0 artifacts (Maven Central), Temurin JDK 21, macOS arm64.
The driver is a throw-away `Spike.java` on the `approach-ardoco` test classpath. No product code changed.
Projects: MediaStore, TeaStore, TEAMMATES. Code model: the ACM from `code-extractor-ardoco` (T2.6).

## Summary

| # | Unknown | Result | Evidence |
|---|---------|--------|----------|
| U1 | TransArC without CoreNLP models | Pass. The run completes without the models jar. Links equal a run with the jar. | MediaStore and TeaStore, SAD-SAM, SAM-code and SAD-code link files are equal. |
| U2 | Component listing | Pass. `ArchitectureConfiguration(file, COMPONENT_LISTING)` works. Component ids equal the second field of each `Name::Id` line. Type is `component`. | MediaStore gold names. |
| U3 | Listing equals in-memory model | Pass. `ExArch` with a stub answer gives the same links as `Transarc` with the listing of the same names. | Link files equal. |
| U4 | Answer replay | Pass. See "Answer replay". | Two offline runs give equal output. |
| U5 | Paper facts | Found. See "Paper facts". | Paper text. |
| U6 | Cost | Measured. See "Cost". | `COST` lines. |
| U7 | ExArch code prompt sees a code model | Reads the code model; code variant not run. See "Paper facts". | Bytecode of `LlmArchitectureProviderInformant`. |

Consequence of U1: TransArC (SAM-code) is possible with the T2.5 path. The T2.1b plan row and T2.5 open question 7 said it stays out.
Both texts are corrected in the same commit as this note.

## U1 - No CoreNLP models

1. Run `Transarc` with `PreprocessingData` injected through `DataRepositoryHelper.putPreprocessingData` (T2.1 path).
2. Use the T2.6 ACM as code input.
3. Compare the classpath without and with the `stanford-corenlp` models jar.

Result: the link sets are equal in both runs. Plain Maven resolution of the `Transarc` classpath does not need the models jar when the preprocessing data exists.
Note: a PCM model gives different links than a names-only listing, because the PCM also holds interfaces. Compare like with like.

## U2 and U3 - Listing

- A listing has one `Name::Id` line per component.
- `Transarc` reads it and creates component entities with the given ids.
- A name or id with `::` or a line break cannot be written. The port drops such names (documented deviation from Java).

## U4 - Answer replay

- `LargeLanguageModel` reads only environment variables. The provider `OLLAMA_GENERIC` accepts a base URL (`OLLAMA_HOST`), a model name (`OLLAMA_MODEL_NAME`) and a token.
- `CachedChatLanguageModel` stores answers in `$LLM_CACHE_DIR/OLLAMA_GENERIC-cache.json`. The key is `messages.toString()` of langchain4j. This key depends on the library version, so a pre-seeded cache is fragile.
- Chosen mechanism for tests: an HTTP stub for `POST /api/chat` (in tests only, never in an agent). The stub returns the recorded answer when the request holds at least two user messages (prompt 1, then prompt 2).
- Run 1 with an empty `LLM_CACHE_DIR` calls the stub. Run 2 with the filled cache needs no stub. Output was equal.
- An empty answer causes a `NullPointerException` in langchain4j. The port treats an empty answer as an error and does not copy this behaviour.

Decision for open question 1: use the HTTP stub (or `llm-replay` behind `OLLAMA_GENERIC` plus `OLLAMA_TOKEN`) in tests.

## Post-processing parity (vector set)

Sixteen answer texts ran through the Java informant. The Java name lists are in `services/extraction-agent/tests/fixtures/exarch/vectors.json`.
Rules the Python port must follow:

| Topic | Java behaviour | Port rule |
|-------|----------------|-----------|
| Line parse | Only lines that start with `- ` after `trim` give a name. | Same. |
| Trim | `String.trim`: removes characters up to U+0020. | Strip characters up to U+0020 only. Do not use `str.strip`. |
| Blank test | `Character.isWhitespace` | Use an explicit set equal to `Character.isWhitespace` (no no-break spaces). |
| Case | First character upper case, rest lower case, per word. | Per code point. Use `str.upper` and `str.lower` on one character and keep Java results (for example U+0130). |
| Affix | Remove `Components`, then `Component`, anywhere in the name. | Same order. |
| Duplicates | Set semantics. | Same. |
| Order | Sort by UTF-16 code units. | Sort by the UTF-16 encoding, not by code point. |
| `::` and line breaks | Not checked. | Drop the name and log it. |

Differences from Java that remain: the `::` and line-break drop, and the empty-answer error.

## Paper facts (U5, U7)

- Title: "Enabling Architecture Traceability by LLM-based Architecture Component Name Extraction".
- Authors: Fuchss, Liu, Hey, Keim, Koziolek (KASTEL, KIT). Venue: not stated in the PDF header. The text is the ICSA 2025 version.
- Replication package: Zenodo, DOI 10.5281/zenodo.14506935, https://zenodo.org/doi/10.5281/zenodo.14506935. The GitHub replication repository licence is MIT.
- Best variant: component names from the SAD only (two prompts, chain of thought). Best model: GPT-4o, weighted average F1 0.86, against 0.87 for TransArC with manual SAMs.
- Other modes: code only (packages), best Llama 3.1 70b, weighted F1 0.81. SAD plus code aggregated with similarity (threshold 0.5), best weighted F1 0.86. Aggregated with a prompt, best GPT-4 Turbo 0.85.
- Conclusion RQ4 of the paper: using only the SAD performs best on average.
- U7: the 2.1.0 informant reads the `CodeModel` from the data repository. It takes `getAllPackages()` for the features `PACKAGES` and `PACKAGES_AND_THEIR_CLASSES`. The code prompt therefore sees the code model. T2.7 offers documentation only (open question 2).

## Cost (U6)

Single JVM, `-Xmx` large, run 0 of the cost mode, `Transarc` against `Ardocode`, with injected preprocessing data and the ACM.

| Project | Runner | Seconds | Peak heap MiB | SAD-code links | SAM-code links | SAD-SAM links |
|---------|--------|---------|---------------|----------------|----------------|---------------|
| TeaStore | transarc | 15.2 (run 1: 14.8) | 419 | 508 | 157 | 20 |
| TeaStore | ardocode | 6.5 (run 1: 6.4) | 415 | 2682 | - | - |
| TEAMMATES | transarc | 115.1 | 848 | 9702 | 808 | 81 |
| TEAMMATES | ardocode | 68.7 | 972 | 18843 | - | - |

Reading: `transarc` costs about 1.7 to 2.3 times the time of `ardocode`. Peak heap is equal or lower. No new heap limit is needed.
The TEAMMATES ACM comes from the `writesTheCodeModelsForTheTlrAgentTests` generator in `CodeModelEquivalenceTest` (2447 files).

## Method note

The driver started a `com.sun.net.httpserver` stub, set `OLLAMA_MODEL_NAME`, `OLLAMA_HOST` and `LLM_CACHE_DIR`, and used the CoreNLP provider container from the `approach-ardoco` tests for text annotation.
