# T2.7 SAD-code metrics

Run date: 2026-10-18.

## Question

Does the Transarc approach with a derived architecture model lose more than 0.05 macro F1 against ardocode?
The derived model is a component listing from the recorded GPT-4o answers.

## Method

- Link type: SAD to code compilation unit.
- Variants:

| Variant | Architecture model |
|---|---|
| ardocode | None. The `Ardocode` runner links sentences to code directly. |
| transarc-gold | The gold PCM repository of the benchmark. |
| transarc-derived | A component listing. It comes from the recorded GPT-4o answers of the ExArch documentation prompt (second turn, `DOCUMENTATION_ONLY_V1` wording). |

- Gold: `goldstandard_sad_*-code_*.csv` of the replication package.
- Gold entries that end with `/` stand for every compilation unit below that path. The script expands them like ArDoCo does.
- Paths are URL-decoded like the ArDoCo benchmark does.
- Metric: precision, recall and F1 over (sentence, compilation unit) pairs. Macro F1 is the mean of the project F1 values.
- Flag rule: flag if macro F1 of transarc-derived is more than 0.05 below macro F1 of ardocode.

## Versions

| Item | Value |
|---|---|
| ArDoCo | 2.1.0 |
| ExArch port tool version | `2.1.0+x1` |
| LLM answers | GPT-4o, recorded cache of the replication package |
| Replication package | Zenodo 10.5281/zenodo.14506935 |
| Code | Benchmark commits of `services/code-extractor-ardoco/src/test/resources/benchmark-code.txt` |
| Preprocessing | CoreNLP of the Sandman CoreNLP service |

## Results

| Project | ardocode P | ardocode R | ardocode F1 | transarc-gold P | transarc-gold R | transarc-gold F1 | transarc-derived P | transarc-derived R | transarc-derived F1 |
|---|---|---|---|---|---|---|---|---|---|
| mediastore | 0.050 | 0.660 | 0.092 | 1.000 | 0.520 | 0.684 | 0.491 | 0.520 | 0.505 |
| teastore | 0.196 | 0.744 | 0.310 | 0.986 | 0.709 | 0.825 | 0.948 | 0.666 | 0.782 |
| teammates | 0.373 | 0.924 | 0.532 | 0.713 | 0.909 | 0.799 | 0.713 | 0.902 | 0.797 |
| bigbluebutton | 0.072 | 0.559 | 0.127 | 0.772 | 0.907 | 0.834 | 0.749 | 0.787 | 0.768 |
| jabref | 0.664 | 1.000 | 0.798 | 0.893 | 1.000 | 0.943 | 0.893 | 1.000 | 0.943 |
| macro | 0.271 | 0.777 | 0.372 | 0.873 | 0.809 | 0.817 | 0.758 | 0.775 | 0.759 |

## Verdict

- Macro F1 of ardocode minus macro F1 of transarc-derived is -0.387.
- The flag is not set. Transarc with a derived model is far above ardocode.
- Transarc-derived is 0.058 below transarc-gold in macro F1. The loss comes mostly from mediastore (0.505 against 0.684).

## Checks

- Transarc-gold equals the ArDoCo expected SAD-code values of the `CodeProject` enum for mediastore, teastore, teammates, bigbluebutton and jabref.
- The expected values are P/R: .995/.515, .999/.708, .705/.909, .765/.905 and .885/.999. Differences are at most 0.01 for mediastore, teastore, teammates and bigbluebutton.
- Jabref transarc-gold P is 0.893 against the expected 0.885.
- Component names of the listings equal the names that the replication log `results/documentation-only.txt` prints for GPT-4o. The comparison ignores letter case, because the port normalizes camel case.

## Not measured

- Ardocode ran without a SAM. The result differs from the ArDoCo expected values, because those values belong to the SAM-based run. The 0.05 rule uses this ardocode run.
- Bigbluebutton uses the gold text `text_2021/bigbluebutton.txt` (120 lines). The recorded GPT-4o answer belongs to the fixture text of 86 lines. Both texts describe the same documentation.
- Only one model run exists for each project. The answers are recorded, so there is no spread.
- The code-aware ExArch prompt is not measured.
- Time and memory are in `spike-T2.7a.md`.

## Reproduce

1. Run `uv run python scripts/metrics_t27.py listings CACHE FIXTURES OUT LOG` to write the component listings.
2. Run `scripts/metrics_t27/MetricsDriver.java` to write the link files. It needs Docker for CoreNLP and the code models of `CodeModelEquivalenceTest`.
3. Run `uv run python scripts/metrics_t27.py table LINKS GOLD ACMS PROJECT...` to print the table.
