# Evaluation T3.9: classic against ArTEMiS

Status: partial. Two projects, two chat models. The verdict is "supported on two projects with a cloud model, not shown for the full set" (see the end).

## Run 1: `qwen3:4b` (local), TeaStore

### Setup

* Date: 2026-11-12. Project: TeaStore (`sandman-example`). Route: `local-only`, chat model `qwen3:4b` on the remote Ollama.
* All `artemis*` approaches and detectors enabled for the run only. The defaults in `config/` are unchanged.
* Gold standard: `goldstandard_sad_2020-sam_2020.csv` (27 links). Tool: `sandman eval compare --format ardoco-sad-sam`.

### Result: trace links, SAD to SAM

| System | Links | Precision | Recall | F1 |
|--------|-------|-----------|--------|----|
| `ardoco` (classic) | 20 | 1.000 | 0.741 | 0.851 |
| `artemis` | 23 | 0.913 | 0.778 | 0.840 |

* F1 difference -0.011, 95 % CI [-0.124, 0.150]. McNemar: 4 links only in `artemis`, 5 only in `ardoco`, p = 1.0.
* Inconsistencies: TeaStore has no gold standard. `programming-language-id` found 3 findings. `artemis-id` and `architectural-style-id` found none that remained.

### Cost and time

| Run | Time |
|-----|------|
| Full workflow without ArTEMiS | 126 to 162 s |
| Full workflow with all ArTEMiS approaches | 1507 s |
| `artemis-id` detect | 429 s |
| `programming-language-id` detect | 247 s |
| `architectural-style-id` detect | 51 s |

The model is local, so the money cost is zero. The time is about ten times higher.

### Not done in run 1

* Benchmark projects beyond TeaStore (beets, scons, zengarden, rod, coronawarnapp, teammates, bigbluebutton) and their goldens in `experiments`.
* Inconsistency precision and recall.
* A run with a stronger chat route.
* `make perf` and the T5.4 F-1 column.

## Run 2: `openai/gpt-6-luna` (cloud, `chat-default`)

* Date: 2026-11-12. Stack: `make up` (docker CLI works; the container tool still returns `XPC connection error`, so all `exec` and `logs` calls use `docker compose`).
* Projects `luna-teastore` (TeaStore, `sandman-example`) and `luna-mediastore` (docs, PCM and UML of the MediaStore benchmark). Both use the project setting `llmPolicy: cloud-allowed`, set with `PUT /v1/admin/projects/{id}`. The committed policies and defaults are unchanged.
* All `artemis*` approaches and the detectors `artemis-id`, `programming-language-id`, `architectural-style-id` were enabled in `config/` for the run only and reverted afterwards.
* The key was accepted by the gateway. All `gpt-6-luna` chat and embedding calls returned HTTP 200. The gateway log shows one HTTP 504 in the same period. It is probably from the slow local `qwen3:4b` check below (not verified).
* The code extractor was the new one (`code-extractor-ardoco`, commit `f938afb`, no repeated `codeFiles`). Every ArTEMiS run read an ACM of 115 files (TeaStore has 115 Java files) and no `no element for file` warning appeared.

### TeaStore: links per approach (active links in the KG)

| Approach | `qwen3:4b` | `gpt-6-luna` | Raw ArTEMiS pairs of the luna run |
|----------|-----------|--------------|-----------------------------------|
| `ardoco` | 40 | 40 | 20 per model |
| `transarc` | 603 | 603 | |
| `artemis` | 46 | 58 | 29 SAD-model pairs per model |
| `artemis-class` | 6 | 7 | 7 SAD-code pairs |
| `artemis-datafile` | 0 | 0 | 0 (TeaStore has no data files) |
| `artemis-transarc` | 0 | 777 | 536 and 547 SAD-code, 115 SAM-code |
| `artemis-exarch` | 0 | 552 | 552 SAD-code, 0 SAM-code |

### TeaStore: findings

| Detector | Result with `gpt-6-luna` | Time of one run |
|----------|--------------------------|-----------------|
| `artemis-id` | 10 findings `undocumented-model-element` (5 per model file: SlopeOneRecommender, PreprocessedSlopeOne, OrderBasedRecommender, DummyRecommender, PopularityBasedRecommender), 0 text findings | 70 s |
| `programming-language-id` | 0 findings (`qwen3:4b`: 3) | 30 s |
| `architectural-style-id` | 0 findings | 142 s |

TeaStore has no inconsistency gold standard. Precision and recall are NOT measured. The 3 language findings of `qwen3:4b` were not reproduced by `gpt-6-luna`, so they are probably false positives of the small model (not verified by hand).

### TeaStore: time

| Run | `qwen3:4b` | `gpt-6-luna` |
|-----|-----------|--------------|
| Full workflow with all ArTEMiS approaches | 1507 s | 564 s |
| Without ArTEMiS (earlier runs) | 126 to 162 s | not run |
| Single ArTEMiS approach (log) | 18 to 96 s per LLM call | 38 to 49 s per approach run |

### SAD to SAM against the 27 gold links (`sandman eval compare`)

| System | Links | Precision | Recall | F1 |
|--------|-------|-----------|--------|----|
| `ardoco` | 20 | 1.000 | 0.741 | 0.851 |
| `artemis` (`qwen3:4b`) | 23 | 0.913 | 0.778 | 0.840 |
| `artemis` (`gpt-6-luna`) | 29 | 0.931 | 1.000 | 0.964 |

* `gpt-6-luna` against `ardoco`: F1 +0.113, 95 % CI [0.003, 0.250], p = 0.045 (Holm 0.090). Recall +0.259, CI [0.107, 0.429]. Precision -0.069, CI [-0.172, 0.000].
* McNemar: 7 links only in `artemis`, 2 only in `ardoco`, p = 0.180.
* The difference is positive. It is not significant after the Holm correction on one project.
* `artemis-class`, `artemis-transarc`, `artemis-exarch` and `artemis-datafile` write no SAD-to-SAM links. Their exports are empty in this format.

### TeaStore: SAD to code (indicative only)

The gold file `goldstandard_sad_2020-code_2022.csv` lists 70 package directories. The export lists class files. The values below reduce each class file to its directory and match exactly. Treat them as rough.

| Approach | Directory pairs | Precision | Recall | F1 |
|----------|-----------------|-----------|--------|----|
| `transarc` (classic) | 90 | 0.089 | 0.114 | 0.100 |
| `artemis-transarc` | 132 | 0.083 | 0.157 | 0.109 |
| `artemis-exarch` | 137 | 0.080 | 0.157 | 0.106 |
| `artemis-class` | 7 | 0.000 | 0.000 | 0.000 |

The values are low for all systems, because the gold lists parent packages that no class file sits in. No system is better in a way that matters here.

### MediaStore (SAD to SAM, 31 gold links)

Input: `mediastore.txt`, `ms.repository` (PCM) and `ms.uml`. The benchmark has no Java source (only an ACM), so only `artemis` ran. Time of the full workflow: 216 s.

| System | Links | Precision | Recall | F1 |
|--------|-------|-----------|--------|----|
| `ardoco` | 18 | 0.944 | 0.548 | 0.694 |
| `artemis` (`gpt-6-luna`) | 34 | 0.912 | 1.000 | 0.954 |

* F1 +0.260, 95 % CI [0.115, 0.435], p = 0.0004 (Holm 0.0008).
* McNemar: 15 links only in `artemis`, 3 only in `ardoco`, p = 0.0075.
* `artemis-id`: 8 findings `undocumented-model-element`. There is no inconsistency gold standard in the vendored benchmark.
* The T3.8 note names 18 against 29 links for the upstream MediaStore run. This run gives 18 `ardoco` links and 34 `artemis` links in the same CSV format. The standalone upstream runner was not run, so equivalence is NOT checked.

### Cost

* The gateway does not give token counts or money to an agent. The admin API (`/metrics`, logs UI) needs the admin credential, which was out of bounds. Token counts and cost are NOT measured.
* Calls to the cloud: about 175 chat completions (`gpt-6-luna`, all HTTP 200) and 201 embedding calls (`text-embedding-3-small`, LiSSA retrieval and extraction), for both projects together. Prompts are sentence-sized or tool-call sized, so the spend is small. An exact value needs the OpenAI usage page.
* Sent to the cloud: only the TeaStore and MediaStore benchmark texts, models and code.

### Why `artemis-transarc` and `artemis-exarch` gave 0 links with `qwen3:4b`

* The cause is NOT the weak model and NOT the wiring. With `gpt-6-luna` both approaches give links (777 and 552).
* A new check ran both approaches on the same project with `qwen3:4b` (project switched to `local-only`). The Tlr agent log shows the approaches now produce raw pairs with the 4B model too: `artemis-transarc` 314 SAD-code and 115 SAM-code pairs, `artemis-exarch` 353 SAD-code pairs.
* The first run with 0 links came before commit `f938afb` (repeated `codeFiles` entries of a Java unit in the code extractor). The old logs are gone, so the link from that bug to the 0 result is inferred, not proven. The fix has a test (`MixedLanguagesTest`). No further code change was needed.
* `artemis-datafile` gives 0 links because TeaStore has no data files.

## Verdict against decision 6

Decision 6 needs ArTEMiS to match or beat classic on the benchmark projects.

| Project | Model | `ardoco` F1 | `artemis` F1 | Result |
|---------|-------|-------------|--------------|--------|
| TeaStore | `qwen3:4b` | 0.851 | 0.840 | equal within noise |
| TeaStore | `gpt-6-luna` | 0.851 | 0.964 | better, not significant after Holm (p 0.045, Holm 0.090) |
| MediaStore | `gpt-6-luna` | 0.694 | 0.954 | better, significant (Holm 0.0008) |

* With `gpt-6-luna`, `artemis` matches or beats `ardoco` on both projects that were run. The time is 564 s against 126 to 162 s for the full workflow, and the model is a cloud model.
* Not evaluated: beets, scons, zengarden, rod, coronawarnapp, teammates, bigbluebutton (not vendored), the SAD-to-code gold (class against package granularity), the inconsistency gold files, the standalone-runner equivalence, `make perf` and the T5.4 F-1 column.
* Verdict: decision 6 is SUPPORTED on two projects with a strong cloud model, and NOT shown for the whole benchmark set or for a local model. Two projects are not the full set. The defaults stay unchanged. No ADR, site or release-notes change.
