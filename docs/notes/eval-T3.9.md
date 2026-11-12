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

## Run with `openai/gpt-6-luna` (cloud): NOT run

Date: 2026-11-12. The task was to repeat the TeaStore run with the `chat-default` route. It was not started.

* Missing: `OPENAI_API_KEY`. It is not set in the environment of the agent. The key belongs in `deploy/secrets/gateway.env`, which agents must not read, so its presence is not verified. Nobody can send cloud traffic until the owner confirms it.
* The stack is not up (only `traverse`, `nats`, `llm-gateway`, `corenlp-provider` run). A run needs `make up` and a project with the `cloud-allowed` policy (the `local-only` policy blocks the cloud route; committed policies stay unchanged).
* The container tool returned `XPC connection error` for `exec` and `logs`, so the gateway could not be inspected either.
* MediaStore was not run for the same reason.

### Why `artemis-transarc` and `artemis-exarch` gave 0 links

* They are not stubs. `ArtemisApproachBase` runs the real ArDoCo stages for both flavors (NER stage, then `SamCodeTraceabilityLinkRecovery` and `SadSamCodeTraceabilityLinkRecovery`; ExArch adds `docArchitecture` with the gateway).
* The two flavors read the code model (`supports()` is `architecture-doc` or `architecture-model` to `code`). `artemis-datafile` gave 0 links because TeaStore has no data files.
* The cause of the 0 links is NOT established. It needs a run with the log (look for the `no element for file` and skip warnings) and with a stronger model. A 4B model that finds few NER entities is a plausible cause, a config or wiring cause is not excluded.

### Verdict

Unchanged: decision 6 is NOT shown. No default changes.
