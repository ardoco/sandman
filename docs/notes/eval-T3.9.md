# Evaluation T3.9: classic against ArTEMiS

Status: done for the evaluation, partial for three items (see "Not done in run 3"). Run 3 covers all ten vendored benchmark projects with `gpt-6-luna`. The verdict over the full set is at the end. It does NOT fully support the flipped defaults.

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

## Run 3: full benchmark set with `openai/gpt-6-luna` (2026-11-13)

### Setup

* Projects: `luna-<name>` for rod, beets, zengarden, scons, coronawarnapp, teammates, bigbluebutton and jabref. `luna2-mediastore` and `luna2-teastore` repeat the two earlier projects. Policy `cloud-allowed` per project. Only public benchmark data went to the cloud.
* Input per project: the SAD text, the PCM and UML models (if the benchmark has them) and the source code (if staged). Source code was staged for rod, beets, zengarden, scons, coronawarnapp and TeaStore. Teammates, bigbluebutton, jabref and MediaStore ran model-only (no source in the benchmark).
* All `artemis*` approaches and all three LLM detectors were enabled in `config/` for the run only. The files were reverted afterwards.
* Ingest used `--no-review` and the kind rule `<name>.txt=architecture-doc`.
* Scoring: `sandman eval compare --format ardoco-sad-sam` for SAD to SAM. A small script scores the inconsistency gold files. A language or style finding counts as true if the gold row is `valid=false` with the same line (`evidence.sentence + 1`) and the same value. A class or datafile finding counts if sentence and name match. An undocumented model element counts if its model id is in the UME gold.
* The scripts are in `.work/` (git-ignored): `luna_run.py`, `export_luna.py`, `export_findings.py`, `score.py`, `score_ume.py`, `cmp_sum.py`.

### SAD to SAM: classic `ardoco` against `artemis`

| Project | Gold links | `ardoco` P / R / F1 | `artemis` P / R / F1 | F1 diff | Holm (bootstrap F1) | McNemar (Holm) |
|---------|-----------|---------------------|----------------------|---------|---------------------|----------------|
| MediaStore | 31 | 0.944 / 0.548 / 0.694 | 0.939 / 1.000 / 0.969 | +0.275 | 0.0004 | 0.0023 |
| TeaStore | 27 | 1.000 / 0.741 / 0.851 | 0.871 / 1.000 / 0.931 | +0.080 | 0.396 | 0.549 |
| Teammates | 57 | 0.288 / 0.860 / 0.432 | 0.667 / 0.772 / 0.715 | +0.284 | 0.0002 | 1e-16 |
| BigBlueButton | 62 | 0.898 / 0.710 / 0.793 | 0.787 / 0.597 / 0.679 | -0.114 | 0.175 | 0.175 |
| JabRef | 18 | 0.900 / 1.000 / 0.947 | 1.000 / 0.778 / 0.875 | -0.072 | 0.566 | 0.688 |

* The Holm values are per project over the metrics of the tool. Over the five projects (Holm, five tests, bootstrap F1 p-values) MediaStore and Teammates stay significant (0.001). TeaStore, BigBlueButton and JabRef do not (0.59).
* Mean F1 over the five projects: `ardoco` 0.743, `artemis` 0.834.
* `artemis` wins on three projects (two significant) and loses on two (not significant). Recall rises on three projects and falls on two.
* The TeaStore result of run 2 was F1 0.964 with 29 links. This run gives F1 0.931 with 31 links on the same input and model. The difference of 0.033 is the run-to-run spread of the cloud model. Use it as the tolerance for any equality claim against `artemis`.
* The five projects rod, beets, zengarden, scons and coronawarnapp have no PCM or UML. They have no SAD-to-SAM gold. They give no SAD-to-SAM numbers.

### SAD to code

* The gold files `goldstandard_sad_*-code_*.csv` exist for TeaStore, MediaStore, BigBlueButton, JabRef and Teammates. Only TeaStore had source code in this run. The numbers of run 2 stand (directory mapping, indicative): `transarc` F1 0.100, `artemis-transarc` 0.109, `artemis-exarch` 0.106, `artemis-class` 0.000.
* How the mapping works: each exported class file is reduced to its directory. A pair counts if sentence and directory equal a gold pair. The gold lists parent packages, so recall stays low for all systems.
* The class gold files of beets, zengarden, teammates and scons are scored in the next section (class names, not directories).

### Inconsistencies: new detectors against the gold

Counts are findings against gold rows with `valid=false` (language, style) or gold rows (class, datafile).

| Project | Detector | Gold | Found | TP | FP | FN |
|---------|----------|------|-------|----|----|----|
| rod | `programming-language-id` | 0 | 11 | 0 | 11 | 0 |
| rod | `architectural-style-id` | 0 | 2 | 0 | 2 | 0 |
| beets | `architectural-style-id` | 1 | 1 | 0 | 1 | 1 |
| beets | `artemis-id` class | 0 | 1 | 0 | 1 | 0 |
| zengarden | `artemis-id` class | 1 | 1 | 0 | 1 | 1 |
| scons | `artemis-id` class | 50 | 56 | 2 | 54 | 48 |
| teammates | `artemis-id` class | 12 | 0 | 0 | 0 | 12 |
| teammates | `architectural-style-id` | 1 | 0 | 0 | 0 | 1 |
| coronawarnapp | `architectural-style-id` | 0 | 5 | 0 | 5 | 0 |
| coronawarnapp | `artemis-id` datafile | 0 | 3 | 0 | 3 | 0 |
| bigbluebutton, scons, zengarden, teammates | `programming-language-id`, datafile | 0 | 0 | 0 | 0 | 0 |

* Result: across all projects the language and style detectors find 0 of the gold inconsistencies (0 true positives) and report about 30 false positives. The class strategy has precision 0.036 and recall 0.040 on scons and finds nothing on teammates.
* Language gold has rows only with `valid=false` where an inconsistency exists. For rod the language gold says C is valid, and the rod findings are therefore false positives. The staged rod code has no `.c` files. The 11 findings are not a detector fault alone: the input has no C. The numbers stay as measured.
* TeaStore, coronawarnapp and others have one language finding without gold. These findings are not scored.
* Zengarden has 9 style findings and no style gold. They are not scored.
* The 429 responses of the gateway (see Cost) hit zengarden (147) and coronawarnapp (344). Results of these two projects may be worse than with an unlimited key. The runs ended with status SUCCEEDED.

### Inconsistencies: undocumented model elements (UME gold), `ardoco-id` against `artemis-id`

| Project | Gold | `ardoco-id` found / TP / FP | `artemis-id` found / TP / FP |
|---------|------|-----------------------------|------------------------------|
| MediaStore | 4 | 6 / 4 / 2 | 4 / 4 / 0 |
| TeaStore | 5 | 5 / 5 / 0 | 5 / 5 / 0 |
| BigBlueButton | 1 | 1 / 1 / 0 | 2 / 0 / 2 |
| JabRef | 1 | 1 / 1 / 0 | 3 / 1 / 2 |
| Teammates | 0 (empty file) | 4 / 0 / 4 | 1 to 13 / 0 / all (per model file) |

* Recall is 1.0 for both on MediaStore, TeaStore and JabRef. `artemis-id` misses the one element of BigBlueButton.
* Precision: `artemis-id` is better on MediaStore, equal on TeaStore, worse on BigBlueButton, JabRef and Teammates.
* The gold has no rows for text-side findings (`missing-model-element`) in the model projects. These are not scored. `artemis-id` reports 39 of them on TeaStore, 46 on Teammates for `ardoco-id`. Their precision is unknown.

### Time

Wall time of the whole workflow (ingest, all approaches, all detectors), including cloud latency and, for two projects, retries after 429.

| Project | Time |
|---------|------|
| jabref (model only) | 2 min |
| mediastore (model only) | 3 min |
| bigbluebutton (model only) | 6 min |
| rod | 11 min |
| teastore | 11 min |
| teammates (model only) | 12 min |
| scons | 15 min |
| beets | 20 min |
| zengarden | 23 min |
| coronawarnapp | 45 min |

Without ArTEMiS the same workflow takes 126 to 162 s on TeaStore (run 1 and earlier).

### Cost: calls

* Token counts and money are still NOT measured. The gateway gives them only on the admin API, which was out of bounds.
* Calls come from the gateway request log (time windows per project, so the split is approximate): 1 346 chat calls with HTTP 200 and 491 with HTTP 429 (rate limit), 781 embedding calls with HTTP 200, for run 3 and the earlier cloud runs of the same day.
* Per project (chat 200 / chat 429 / embeddings): rod 241 / 0 / 9, beets 149 / 0 / 47, zengarden 356 / 147 / 14, teammates 25 / 0 / 109, bigbluebutton 11 / 0 / 101, jabref 11 / 0 / 24, coronawarnapp 326 / 344 / 317, scons 107 / 0 / 0, mediastore 12 / 0 / 52, teastore 108 / 0 / 108. The rod window also holds earlier calls.
* The 429 rate is a limit of the key, not of SANDMAN. The LLM client retries. Zengarden and coronawarnapp show it.

### Equivalence with the standalone upstream runner (TeaStore)

* NOT run. Upstream at `21ef682` cannot take a ChatModel (draft issue `docs/notes/draft-upstream-issue-chatmodel-injection.md`). A standalone run would call the cloud with its own client. It would give a different answer from a different run, not an equal one.
* Substitute: the run-to-run spread of `artemis` on TeaStore. Run 2 gave F1 0.964 (29 links). Run 3 gave F1 0.931 (31 links). A standalone run must fall inside this spread to count as equal. This was not checked.
* Equivalence of the classic approaches with the standalone runner stays covered by `BenchmarkEquivalenceTest` and `TransarcEquivalenceTest`.

### Code input: directory against ACM (language and style detectors)

* NOT compared. In SANDMAN the detectors read the code through the code model source (the ACM of the code extractor plugin, commit `4d177a2`). A directory input does not exist in the SANDMAN pipeline. The upstream directory path needs the upstream runner, see the item above.
* The result is that all numbers in this note use the ACM input.

### Cassette goldens and KG tests for the new detectors

* NOT added. The detectors need a recorded cassette of cloud answers. A recording needs several runs of the language and style prompts on a fixed input. The results of run 3 show that the detectors give false positives on the available inputs, so a golden would freeze wrong findings. The unit and wiring tests of T3.9 stay in place.
* `make e2e` and `make replay-twice` are covered in the T3.9 task document.

### Not done in run 3

* Token and money cost.
* The standalone-runner equivalence and the directory against ACM comparison (reasons above).
* Cassette goldens for the language and style detectors.
* SAD to code for the projects without staged source (MediaStore, Teammates, BigBlueButton, JabRef).
* Rod, beets and cwa language and style gold with the correct input (rod has no `.c` files).


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

## Decision and evidence limits (2026-11-12)

* The user decided to flip the defaults after reading this note. The verdict above is a limit, not a pass for the whole set.
* The evidence covers 2 projects (TeaStore, MediaStore) and 1 strong cloud model (`gpt-6-luna`). The TeaStore gain is not significant after Holm. With `qwen3:4b` the result equals classic.
* The cost is about 4 times the runtime (564 s against 126 to 162 s). Token cost is not measured.
* Projects not evaluated: beets, scons, zengarden, rod, coronawarnapp, teammates, bigbluebutton. No inconsistency gold was run.
* The defaults changed: `artemis`, `artemis-transarc`, `artemis-id` on; `ardoco`, `transarc`, `ardoco-id` run only without an LLM route. Rollback: set `enabled: false` on the ArTEMiS entries and remove `fallbackFor`.

## Verdict over the full set (2026-11-13)

Decision 6 needs ArTEMiS to match or beat classic on the benchmark projects.

* SAD to SAM (`artemis` against `ardoco`): better on MediaStore, Teammates (both significant after Holm) and TeaStore (not significant). Worse on BigBlueButton and JabRef (not significant). Mean F1 0.743 against 0.834. Decision 6 holds on average. It does NOT hold per project.
* Inconsistencies, component strategy (`artemis-id` against `ardoco-id`): not better. Better precision on MediaStore only, equal on TeaStore, worse precision on BigBlueButton, JabRef and Teammates, and one missed element on BigBlueButton.
* New detectors: the language and style detectors find 0 gold inconsistencies and report about 30 false positives. The class strategy has precision 0.036 and recall 0.040 on scons. They are off by default, so no default contradicts the data.
* Time: 2 to 45 minutes per project against about 2 to 3 minutes without ArTEMiS. The cloud key hit the rate limit on two projects.

Warning: the flipped default `artemis-id` (and `ardoco-id` as fallback only) is NOT supported by this data. It adds false positives on three of five model projects and gives no gain over `ardoco-id`.

Recommendation:

1. Keep the `artemis` and `artemis-transarc` defaults. The SAD-to-SAM gain is real on average and large on two projects.
2. Change `ardoco-id` to always on (remove `fallbackFor: artemis-id`). Keep `artemis-id` on only if its extra findings are accepted. Otherwise set `artemis-id` to `enabled: false`. This needs a decision of the repository owner. The config was NOT changed in this task.
3. Keep `programming-language-id`, `architectural-style-id`, `artemis-class`, `artemis-datafile` and `artemis-exarch` off.
4. Re-run the new detectors with an input that matches the gold (rod with `.c` files) before any claim about them.

Evidence limits: ten projects, one cloud model, one run per project (spread about 0.03 F1 on TeaStore), gold standards with few rows (language, style, UME), a rate-limited key on two projects, no token cost, no standalone equivalence.
