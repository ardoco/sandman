# Evaluation T3.9: classic against ArTEMiS

Status: partial. One project, one model. The verdict is "not proven".

## Setup

* Date: 2026-11-12. Project: TeaStore (`sandman-example`). Route: `local-only`, chat model `qwen3:4b` on the remote Ollama.
* All `artemis*` approaches and detectors enabled for the run only. The defaults in `config/` are unchanged.
* Gold standard: `goldstandard_sad_2020-sam_2020.csv` (27 links). Tool: `sandman eval compare --format ardoco-sad-sam`.

## Result: trace links, SAD to SAM

| System | Links | Precision | Recall | F1 |
|--------|-------|-----------|--------|----|
| `ardoco` (classic) | 20 | 1.000 | 0.741 | 0.851 |
| `artemis` | 23 | 0.913 | 0.778 | 0.840 |

* F1 difference -0.011, 95 % CI [-0.124, 0.150]. McNemar: 4 links only in `artemis`, 5 only in `ardoco`, p = 1.0.
* Inconsistencies: TeaStore has no gold standard. `programming-language-id` found 3 findings. `artemis-id` and `architectural-style-id` found none that remained.

## Cost and time

| Run | Time |
|-----|------|
| Full workflow without ArTEMiS | 126 to 162 s |
| Full workflow with all ArTEMiS approaches | 1507 s |
| `artemis-id` detect | 429 s |
| `programming-language-id` detect | 247 s |
| `architectural-style-id` detect | 51 s |

The model is local, so the money cost is zero. The time is about ten times higher.

## Verdict against decision 6

Decision 6 needs ArTEMiS to match or beat classic on the benchmark projects. This run covers one project. The F1 values are equal within noise. The time is ten times higher. The upstream benchmark projects and gold standards are not vendored. The inconsistency gold files are not evaluated.

Verdict: decision 6 is NOT shown. The defaults stay unchanged. No ADR, site or release-notes change.

## Not done

* Benchmark projects beyond TeaStore (beets, scons, zengarden, rod, coronawarnapp, teammates, bigbluebutton) and their goldens in `experiments`.
* Inconsistency precision and recall.
* A run with a stronger chat route.
* `make perf` and the T5.4 F-1 column.
