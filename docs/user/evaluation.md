# Evaluation and reproduction study

Goal: run the Memgraph integration tests and the reproduction study (task T2.4) on your machine.
The rules of the study are in `experiments/PROTOCOL.md`. The task specification is `docs/tasks/T2.4-reproduction-study.md`.
Run all commands from the root of a checkout, unless a step says otherwise.

## What you check

| Check | Question | Needs |
|---|---|---|
| Memgraph integration tests | Do the query service and the graph queries work on a real Memgraph? | Docker, uv |
| Exact equality (ArDoCo) | Does the adapter give the same links as the standalone ArDoCo runner? | Docker, JDK, Maven, uv; for the live part also the stack and CoreNLP |
| LiSSA compare | How far is a live LiSSA run from the published F1? | the stack, an LLM endpoint |
| Variance pilot, contamination probe | How many runs do you need? Does the model memorize the benchmark? | the stack, an LLM endpoint |

## 1. Prerequisites

1. Install Docker with Compose v2. Start the Docker daemon. Check it with `docker info`.
2. Install [uv](https://docs.astral.sh/uv/). Run `uv sync` once. Expected result: the command ends without an error.
3. Install a JDK and Maven. Run `mvn -v`. Expected result: the command prints the Java and Maven versions. This is only needed for step 4.
4. Get the KIT CoreNLP credentials from the KIT operator. This is only needed for the live stack in mode `remote`.
5. Get an LLM endpoint. This is only needed for LiSSA, pilot and contamination runs. See [getting-started.md](getting-started.md), section 2.

Set up the CoreNLP credentials for the stack as follows.

1. Run `./deploy/gen-secrets.sh`. The command is the same as `make secrets`. It creates `deploy/secrets/corenlp_credentials` with the placeholder `change-me:change-me`.
2. Replace the content of `deploy/secrets/corenlp_credentials` with one line `user:password`. Do not add a second line.

Warning: `make up` stops with a `check-env` error while the file holds the placeholder and the mode is `remote`.

If you have no KIT credentials, use a local CoreNLP container instead.

1. Create `deploy/.env` from `deploy/.env.example`, if the file does not exist.
2. Set `SANDMAN_CORENLP_MODE=local` in `deploy/.env`. The Makefile then adds the compose profile `corenlp-local`.

More details are in [getting-started.md](getting-started.md), section 3.

## 2. Memgraph integration tests

```sh
uv run pytest services/query-service -q -rs
```

- Expected result: all tests pass. The first run pulls the image `memgraph/memgraph:3.13.1` (Testcontainers) and takes longer.
- Docker must be running. The tests start and stop their own Memgraph container. They do not use the stack of `make up`.
- If Docker is not reachable, the Memgraph tests are skipped with the reason `no Docker daemon reachable`. The `-rs` flag prints this reason.
- A skip is not a pass. If you see skips, start Docker and run the command again.

## 3. Start the stack and ingest a benchmark project

1. Start the stack.

   ```sh
   make up
   ```

   Expected result: the first start builds all images and takes several minutes. Then `curl http://127.0.0.1:8080/readyz` prints `{"status":"ok",...}`.
   The command `curl -I http://127.0.0.1:8080` can print `404`. This is normal.

2. Create an admin token.
   1. Open `http://127.0.0.1:8080/console/login`. Log in as `admin` (password: see [getting-started.md](getting-started.md), section 5).
   2. Open the page **Tokens**. Enter a name. Press **Create**. Copy the secret. It is shown once.
   3. Set the two variables in your shell.

   ```sh
   export SANDMAN_URL=http://127.0.0.1:8080
   export SANDMAN_TOKEN=sdm_<the secret you copied>
   ```

   `SANDMAN_URL` is optional. The default is `http://127.0.0.1:8080`. If you set it, it must start with `http://`.
   Type the real secret. The `...` and `<...>` in these pages are placeholders, not text to type.
   The token must belong to an admin user, because step 3 uses an admin endpoint.

3. Create the project.

   ```sh
   curl -X PUT "$SANDMAN_URL/v1/admin/projects/mediastore" \
     -H "Authorization: Bearer $SANDMAN_TOKEN" -H 'Content-Type: application/json' \
     -d '{"name":"MediaStore","llmPolicy":"local-only"}'
   ```

   Expected result: HTTP 200 with the project as JSON. `llmPolicy` is `cloud-allowed` or `local-only`.

4. Ingest the benchmark project. The files are in `services/extraction-agent/tests/fixtures/benchmark/<project>/`.
   The projects are `mediastore`, `teastore`, `teammates`, `bigbluebutton` and `jabref`.

   ```sh
   uv run sandman ingest --project mediastore --mode full --revision benchmark \
     --kind-rule '*.txt=architecture-doc' --kind-rule 'pcm/**=architecture-model' \
     services/extraction-agent/tests/fixtures/benchmark/mediastore
   ```

   Expected result on stderr: `4 files: architecture-doc=1, architecture-model=1, other=2`. The command then prints the ingestion as JSON.

   Why the rules are needed: a file gets the kind `other` unless a rule matches. The benchmark directories have no `sandman.yaml`.
   Without rules, all files are `other`, and ArDoCo finds no document and no model. This is why a first ingest shows only `other=4`.
   The kind of a path is fixed when the artifact is first created. Later rule changes do not change existing artifacts.
   Use a new project id, or a new project, if you ingested with wrong rules.

   The rules follow the same logic that the tests use: the text file `<project>.txt` is an `architecture-doc`, and a model file is an `architecture-model`.
   - The glob `*.txt` matches the text file in the root of the directory.
   - The glob `pcm/**` selects the PCM models. The UML models in `uml/` and the file `NOTICE` stay `other`.
   - Use only one model format per project. PCM and UML ids can differ, and ArDoCo runs once per model. Both would mix into one link set.
   - `teammates` has two PCM files (`teammates.repository` and `teammates-with-details.repository`). The rule `pcm/**` selects both. To select one, use `--kind-rule 'pcm/teammates.repository=architecture-model'`.
   - To use UML, replace the second rule with `--kind-rule 'uml/**=architecture-model'`.
   - Alternative: put the rules as `kindRules` into a `sandman.yaml` in the directory. The format is in [ingesting-artifacts.md](ingesting-artifacts.md). Do not edit the fixture directory. Copy it first.

   Open item: the repository does not state how the benchmark KG input was built for the live stack. The rules above are derived from the CLI code and the test fixtures, and the kind assignment was checked offline with `sandman_cli.kind_of`. A live ingest and ArDoCo run with these rules has not been verified here (no Docker).

5. Wait for the workflow.

   ```sh
   uv run sandman workflows --project mediastore
   uv run sandman workflows --project mediastore --wait wf_<id>
   ```

   The first command lists the workflows. Copy the id (`wf_` and 32 hex characters). The second command waits (default timeout 600 s, option `--timeout`).
   Expected result: it prints the workflow as JSON. Exit code 0 means `SUCCEEDED`. Exit code 1 means another final status.
   The workflow also runs the other enabled approaches of `config/tlr/approaches.yaml` (`stub-name-match`, `lissa`, `ardocode`). Some of them need an LLM or code. A failed step of such an approach does not remove the `ardoco` links. Check the step status in the JSON.

6. Export the links of the approach `ardoco`.

   ```sh
   uv run sandman export tracelinks --project mediastore --format ardoco-sad-sam \
     --approach ardoco > mediastore-live.csv
   ```

   The approach names are `stub-name-match`, `lissa`, `ardoco` and `ardocode` (`config/tlr/approaches.yaml`). The name `ardoco` is the id in `ArdocoApproach`. `ardoco-tlr` does not exist.
   Expected result: a CSV file with the header `modelElementID,sentence` and one row per link.
   The export formats are `ardoco-sad-sam`, `ardoco-sad-code` and `lissa`. The option `--code-root PREFIX` is for `ardoco-sad-code` only.

## 4. Exact equality of ArDoCo

The two checks use the same `sandman eval equal` command.

| Check | Expected file | Actual file | Proves |
|---|---|---|---|
| A. In-test adapter against standalone | `<name>.standalone.csv` | `<name>.adapter.csv` | The adapter code (rendering, sentence split, id mapping) gives the links of the standalone ArDoCo runner. |
| B. Live KG export against standalone | `<name>.standalone.csv` | the live export of step 3.6 | The whole stack (ingestion, extraction, KG, export) gives the same links. |

Run the test first.

```sh
mkdir -p out
SANDMAN_BENCHMARK_CSV_DIR=$PWD/out \
mvn -B -pl services/tlr-agent/approach-ardoco -am verify \
  -Dtest=BenchmarkEquivalenceTest -Dsurefire.failIfNoSpecifiedTests=false
```

- The flag `-Dsurefire.failIfNoSpecifiedTests=false` is needed because `-am` also builds the module `approach-spi`. That module has no test with this name. Without the flag, Maven stops there with `No tests matching pattern`.
- You can also pass the directory as a system property: `-DSANDMAN_BENCHMARK_CSV_DIR=$PWD/out`.
- The test starts its own CoreNLP provider container with Testcontainers. It needs Docker. It does not need KIT credentials and does not use the stack.
- Expected result: `BUILD SUCCESS`. The log shows `BENCHMARK <project>/<model>: ... links ...` for each compared case.
- A case whose raw text splits into more sentences than the rendering has lines is printed as `BENCHMARK EXCLUDED`. Only the required cases (the first three in the table) must not be excluded.

The test writes two files per compared case into the directory. The file name is `<project>-<model file name>.<side>.csv`.

| Case (project, model) | Required | Standalone file | Adapter file |
|---|---|---|---|
| mediastore, `pcm/ms.repository` | yes | `mediastore-ms.repository.standalone.csv` | `mediastore-ms.repository.adapter.csv` |
| mediastore, `uml/ms.uml` | yes | `mediastore-ms.uml.standalone.csv` | `mediastore-ms.uml.adapter.csv` |
| teastore, `pcm/teastore.repository` | yes | `teastore-teastore.repository.standalone.csv` | `teastore-teastore.repository.adapter.csv` |
| teastore, `uml/teastore.uml` | no | `teastore-teastore.uml.standalone.csv` | `teastore-teastore.uml.adapter.csv` |
| teammates, `pcm/teammates.repository` | no | `teammates-teammates.repository.standalone.csv` | `teammates-teammates.repository.adapter.csv` |
| teammates, `uml/teammates.uml` | no | `teammates-teammates.uml.standalone.csv` | `teammates-teammates.uml.adapter.csv` |
| bigbluebutton, `pcm/bbb.repository` | no | `bigbluebutton-bbb.repository.standalone.csv` | `bigbluebutton-bbb.repository.adapter.csv` |
| bigbluebutton, `uml/bbb.uml` | no | `bigbluebutton-bbb.uml.standalone.csv` | `bigbluebutton-bbb.uml.adapter.csv` |
| jabref, `pcm/jabref.repository` | no | `jabref-jabref.repository.standalone.csv` | `jabref-jabref.repository.adapter.csv` |
| jabref, `uml/jabref.uml` | no | `jabref-jabref.uml.standalone.csv` | `jabref-jabref.uml.adapter.csv` |

Files of excluded cases are not written.

Check A. Repeat the command for each file pair you have.

```sh
uv run sandman eval equal --format ardoco-sad-sam \
  --expected out/mediastore-ms.repository.standalone.csv \
  --actual out/mediastore-ms.repository.adapter.csv
```

Check B. Use the live export of step 3.6 for the project `mediastore` and the PCM model.

```sh
uv run sandman eval equal --format ardoco-sad-sam \
  --expected out/mediastore-ms.repository.standalone.csv \
  --actual mediastore-live.csv
```

Output and exit codes of `sandman eval equal`:

| Exit code | Meaning |
|---|---|
| 0 | The link sets are equal. There is no tolerance. |
| 1 | The sets differ. |

The command always prints JSON: `{"equal": true|false, "onlyExpected": [...], "onlyActual": [...]}`. The two lists name the links that only one side has. Each link is a pair `[modelElementID, sentence]`.
Expected result: exit code 0 and empty lists for each pair. Any difference fails the reproduction (protocol section 4).

Caution: for check B, the ingested model must be the same file as in the test case, and the live run must use the same CoreNLP output. If the provider versions differ, the sets can differ without a code defect.

## 5. LiSSA, pilot, perturbation, contamination

All four commands work offline on files and numbers. The live runs that give the input need an LLM endpoint.

| Task | Command |
|---|---|
| LiSSA delta against the published F1 | `uv run sandman eval compare --format lissa --gold GOLD.csv --a lissa-live.csv --published 0.xx` |
| Run count from pilot runs | `uv run sandman eval pilot --f1 0.71 --f1 0.73 --f1 0.70 --f1 0.72 --f1 0.74` |
| Rename identifiers | `uv run sandman eval perturb --names names.txt --out perturbed/ --seed 20261017 FILE...` |
| Memorization verdict | `uv run sandman eval contamination --original 0.71 0.73 0.70 --perturbed 0.60 0.62 0.59` |

Notes per command:

- `compare`: export `lissa-live.csv` with `uv run sandman export tracelinks --project <id> --format lissa --approach lissa`. `GOLD.csv` is the `answer.csv` of the LiSSA replication package. The repository does not include it. The options `--b FILE` (repeatable), `--seed` and `--resamples` are optional. Expected result: JSON with the metrics, the 95 % bootstrap CI and the delta to the published F1. Run LiSSA n times, as `pilot` tells you, and record delta and CI.
- `pilot`: give at least 2 values. The protocol uses 5 runs on the smallest project. The option `--half-width` has the default 0.02. Expected result: one JSON line with `pilotRuns`, `mean`, `sd`, `runs` and `capped`. If `capped` is true, n exceeds the cap of 20. Report this limitation.
- `perturb`: `names.txt` holds one identifier per line. The command writes each input file (same file name) and `mapping.json` into `--out`. Whole identifiers only. The same seed gives the same mapping. Ids and gold standards stay unchanged. The command renames files in `--out` only. Copy the other project files into the same directory before you ingest it as a new project.
- `contamination`: give at least 2 values for each variant. Expected result: JSON with `drop`, `ci`, `baselineSd` and `flagged`. `flagged` is true if the drop exceeds 2 standard deviations and the CI excludes 0.
- The paraphrase step of protocol section 6 is manual. The harness does not include it.

## 5a. Guided script for the live runs

One script leads you through all checks. Start it from the root of a checkout.

```sh
uv run python scripts/t24_guided.py
```

| Step | Check |
|---|---|
| 0 | Preflight: `SANDMAN_URL`, token, proxy, `/readyz`, Docker, admin rights, CoreNLP mode. |
| 1 | Memgraph integration tests. |
| 2 | ArDoCo check A: `BenchmarkEquivalenceTest`, then `eval equal` for each written pair. |
| 3 | ArDoCo check B for `mediastore` and `teastore` (PCM). |
| 4 | LiSSA live re-run, compared with the published F1. |
| 5 | Variance pilot. |
| 6 | Contamination probe. |

- Each step says what you do by hand. Press Enter to run the automatic part. The step then prints `PASS`, `FAIL` or `SKIP`.
- Skip any step. Redo any step with `--only STEP...`.
- Use `--resume [RUN_ID]` to continue a run. Use `--yes` for no prompts. Give the inputs as flags (see `--help`).
- Use `--dry-run` to print the commands without a call to the stack.
- Steps 4 to 6 need an LLM endpoint. Each run creates a new project. Set the policy with `--llm-policy local-only` (default `cloud-allowed`).
- `INPUT_DIR` holds the requirements, the code under `src/` and a `sandman.yaml` with `kindRules`. See the `mini` example `tests/e2e/fixtures/mini/s1`.
- The code file names must be unique. LiSSA links use file names only.
- The paraphrase step of the contamination probe is manual (protocol section 6).
- If a workflow fails, the script saves the workflow JSON and prints the failed steps with the likely cause.
- Raw outputs go to `experiments/live/<run-id>/`. The script writes `report.md` there.
- After each step, the script updates the Results table in `docs/tasks/T2.4-reproduction-study.md`. It writes only values from real command output.

## 6. Troubleshooting

| Message or symptom | Cause | Action |
|---|---|---|
| `sandman: [Errno 8] nodename nor servname provided, or not known` | `SANDMAN_URL` has no `http://`, or a proxy blocks the address. | Set `SANDMAN_URL=http://127.0.0.1:8080`. Set `NO_PROXY=127.0.0.1,localhost`. Unset `http_proxy`, `https_proxy` and `all_proxy` for this shell. On macOS, also check the system proxy settings. |
| `404` with `project X not found` | The project does not exist. | Create the project (step 3.3). Check the project id. |
| `sandman: error: no token: set SANDMAN_TOKEN or pass --token-file` | The variable is empty in this shell. | Run `export SANDMAN_TOKEN=sdm_<secret>` again, or use `--token-file FILE`. |
| `command not found: sandman` | The CLI is not on your `PATH`. | Run `uv run sandman ...` from the checkout. |
| Maven `No tests matching pattern` | `-am` builds a module without that test. | Add `-Dsurefire.failIfNoSpecifiedTests=false`. |
| `curl -I $SANDMAN_URL` prints `404` | The root path has no page. | None. This is normal. Use `/readyz`. |
| `403` on `PUT /v1/admin/projects/...` | The token does not belong to an admin user. | Create the token as `admin` in the console. |
| `make up` prints `check-env: ... placeholder` | `corenlp_credentials` still holds `change-me:change-me`. | Write `user:password` into the file, or set `SANDMAN_CORENLP_MODE=local` (section 1). |
| Memgraph tests are skipped | No Docker daemon. | Start Docker. Run the command of section 2 again. |
| `ingest` prints `other=4` and ArDoCo finds no links | No kind rule matched. | Add the `--kind-rule` options of step 3.4 and use a new project. |
| `eval equal` prints exit code 1 | The link sets differ. | Read `onlyExpected` and `onlyActual`. Do not accept a difference. See check B caution. |

More problems with the stack: see [operations.md](operations.md).

## 7. Where results go and how to finish T2.4

1. Keep the result files under a new directory `experiments/<name>/`. Create `experiments/<name>/experiment.yaml` with `scripts/experiment_manifest.py`. It requires `--name` and `--project`. Add `--gold FILE` for each gold standard. Run it with `--help` to see the other options. The manifest stores the hash of `experiments/PROTOCOL.md`.
2. Archive the standalone and adapter CSV files, the KG export, the output of every `sandman eval` command, and the LLM cassettes with the manifest.
3. Name the model, the provider and the date of each LiSSA run.
4. If a result deviates from the protocol, add a line to the deviation log at the end of `experiments/PROTOCOL.md`.
5. The guided script updates the line `Status:` and the Results table in `docs/tasks/T2.4-reproduction-study.md`. Check the text after the last run.
