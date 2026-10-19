# Evaluation protocol (D-8)

Version: 1 · Date: 2026-10-17 · Source: `docs/stage-3-implementation-plan.md` §3.3, `docs/tasks/T2.3-evaluation-harness.md`

This file is pre-registered. Commit it before the first comparative experiment. The experiment manifest stores its SHA-256 (`evaluation.protocolSha256`). If you change this file after data collection, add an entry to the deviation log at the end and report the deviation with the results.

## 1. Data and gold standards

- Trace link benchmarks: the five `ArDoCo/Benchmark` projects `mediastore`, `teastore`, `teammates`, `bigbluebutton`, `jabref`, at the commit named in `services/extraction-agent/tests/fixtures/benchmark/SOURCES.txt`.
- Gold standards: `goldstandard_sad_*-sam_*.csv` (`modelElementID,sentence`) and `goldstandard_sad_*-code_*.csv` (`sentenceID,codeID`). LiSSA: `answer.csv` of the replication package of each data set.
- A link is a pair. SAD to SAM: (1-based sentence, model element id). SAD to code: (1-based sentence, file path relative to the code root). LiSSA: (source file name, target file name).
- Export formats: `ardoco-sad-sam`, `ardoco-sad-code`, `lissa` (`sandman export tracelinks --format ...`).
- At least one non-public or post-cutoff project must be part of every LLM comparison. Name it in the manifest. Without it, report the comparison as "public data only".

## 2. Metrics

- Primary: F1 of the link set per project. Also report precision and recall.
- F2 is reported for recall-oriented use only. It is not a test target.
- Links of the same pair from several approaches are not merged. Compare one approach at a time.

## 3. Statistics

Per project, for each pair of systems A and B:

1. Paired bootstrap. Resample the union of gold links and predicted links (of A and B) with replacement. Use 10 000 resamples and a fixed seed (default 20261017). Report the point difference B minus A and the 95 % percentile CI for precision, recall and F1.
2. McNemar test (exact, two-sided binomial) on link-level decisions. A decision is correct if it equals the gold standard. Pairs outside the union are correct for both systems, so the union gives exact counts b and c.
3. Effect sizes: the metric difference, Cohen's g = b / (b + c) minus 0.5, and the odds ratio b / c (with 0.5 added to both counts when one is zero). For run-level values, report Cliff's delta.
4. Holm correction across all McNemar and bootstrap-derived p-values of one experiment family. A family is all comparisons of one manifest. Report raw and adjusted p-values. The significance level is 0.05.

Across projects:

5. Wilcoxon signed-rank test (exact, two-sided) only with at least 6 projects. With 5 projects the smallest possible p-value is 0.0625, so no test is run. Report the per-project differences and their CIs instead.

LLM approaches:

6. The number of live runs per configuration comes from the variance pilot (section 5). Do not use a fixed number.
7. Report the mean and the standard deviation over runs. Compute the bootstrap CI over links for each single run and for the run with the median F1.

## 4. Reproduction

- ArDoCo is deterministic. Its link set must be identical to the standalone runner on the same inputs. There is no tolerance. Compare the sets as pairs (section 1). Any difference fails the reproduction.
- LLM approaches (LiSSA): the published runs used models that were not recorded, so replay cannot reproduce them. Run live with the closest available model. Report delta = F1(live) minus F1(published) with the 95 % bootstrap CI of the live F1 (shifted by the published value). Record this run for replay afterwards. Name the model, the provider and the date.
- If the CI of the delta contains 0, call the reproduction consistent. Otherwise report it as a deviation and give the likely cause (model, prompt, data version).

## 5. Variance pilot

- Run each LLM configuration n0 = 5 times on the smallest project. Use the same seed settings as the study.
- Compute the standard deviation s of F1 over the runs.
- The number of runs per configuration is n = ceil((t * s / h)^2), with the t-quantile for n0 - 1 degrees of freedom and the target half-width h = 0.02 F1. Use at least 3 and at most 20 runs. If n exceeds 20, report the limitation.
- Report the pilot values with the study.

## 6. Contamination probe

- Perturb the benchmark: rename identifiers consistently in text, model names and code names (the same mapping everywhere; ids and gold standards stay unchanged), and paraphrase the text (LLM-made, then checked by one person).
- Run the original and the perturbed project the same number of times (section 5).
- Flag memorization if the F1 drop (original minus perturbed) is larger than 2 times the run-to-run standard deviation and the 95 % Welch CI of the drop excludes 0.
- Deterministic approaches (ArDoCo) are not probed.

## 7. NL-to-Cypher

- Split the data by template and by project, so near-duplicates cannot cross the split.
- People who do not tune the prompts write the test questions.
- Execution accuracy compares result multisets without truncation. Do not use `LIMIT`.
- Report the intent-template stub as the baseline.

## 8. Experiment manifest

Each experiment has `experiments/<name>/experiment.yaml` from `scripts/experiment_manifest.py` (Git commit, image digests, configuration hashes, routes, snapshot hashes, LLM mode, seeds). Add `--gold` for every gold standard file. Archive cassettes, the KG export and the metrics output with it.

## Deviation log

| Date | Section | Change | Reason |
|---|---|---|---|
| – | – | none | – |
