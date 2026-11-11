# T-classification-review Classification overview and correction
Status: plan v3, not implemented. Decisions Q1 to Q11 are applied (sections 9 and 10). Waits for the start signal.

## 1. Problem
Every artifact gets a kind: `requirements`, `architecture-doc` (SAD), `architecture-model` (SAM), `documentation` (new), `code`, `test`, `other`. The TLR approaches pair artifacts by kind (`tlr.kindPairs` in `config/orchestrator/rules.yaml`). A wrong kind gives wrong or missing trace links.

Today the user cannot see or correct the kinds before or after an ingestion.

| Fact | Where |
|---|---|
| Kind rules (`glob=kind`, `sandman.yaml`) are the only way to correct a kind. | `tools/cli/.../__main__.py` (`kind_rule`, `ingest`) |
| The extraction agent guesses the kind for files that no rule matches. | `services/extraction-agent/.../classify.py` (`guess`) |
| The CLI does not run the guess. It prints `other` for every file without a rule. | `kind_of` in the CLI |
| The CLI uploads everything except dot-files. `node_modules`, `target`, `build` are uploaded. | `scan` in the CLI |
| The graph stores `kind` and `format` on `Artifact`, but not why. | `contracts/kg/schema.yaml` |
| The dashboard shows no per-file kind. The desktop app shows no kind before upload. | |

## 2. Goals and non-goals
Goals:
1. **One classifier.** CLI, desktop app, dashboard and extraction agent run the same code. Section 3.1 lists the safeguards.
2. Each file gets kind, format, source (`rule`, `heuristic`, `default`), reason and a weak flag. The graph stores them.
3. The user views a folder-level proposal and corrects it. The same features exist in CLI, desktop app and dashboard (section 3.7).
4. A correction is saved as rules in `sandman.yaml`, inside a clearly marked managed block. The server stores no rules.
5. `ingest` warns if the user did not review the classification, and asks before it proceeds.

Non-goals (first version):
* No machine learning or LLM classifier. Heuristics only.
* No git clone. The user still points the CLI at a directory.
* No server-side storage of corrections.

## 3. Design

### 3.1 One shared classifier (uniform result)
Move `classify.py` to `libs/python/sandman-agent` as `sandman_agent.classify`. The CLI, extraction agent and dashboard already depend on this library. There is exactly one copy of the code.

New function next to `guess`:

```python
CLASSIFIER_VERSION = 1       # raise on every behavior change

@dataclass(frozen=True)
class Verdict:
    kind: str
    format: str | None
    source: Literal["rule", "heuristic", "default"]
    reason: str          # "suffix .java", "path segment 'docs'", "2 REQ headings", "rule docs/legacy/**", "no signal"
    weak: bool           # True for "docs dir" and "default": user should look at it
```

`guess` stays and calls the new function. Both the CLI and the server resolve one file the same way: rules first (the manifest rules), then the heuristic.

Safeguards. Each one has a test:

| # | Safeguard | Effect |
|---|---|---|
| 1 | Single module, one resolve function used by the server and all clients | No second implementation to drift. |
| 2 | Golden cases `contracts/classification/cases.json` (path, first bytes, rules, expected verdict) | The server tests and the CLI tests read the same file. The file records `classifierVersion`. The test fails if outputs change and the version stays the same. |
| 3 | The manifest carries `classifierVersion` (optional field, contract change) | If the server version differs, the ingestion gets a warning. The CLI prints it. |
| 4 | The server returns kind counts in the ingestion result | The CLI compares them with its preview and prints a warning on any difference. |
| 5 | e2e test on `tests/e2e/fixtures/mini` and a second fixture without rules | Preview kinds equal graph kinds, file by file. |
| 6 | The dashboard and the desktop app never compute kinds themselves | They show data from the shared module (dashboard: Python endpoint; desktop: CLI JSON). |

The server stays the authority. If the CLI is older than the server, safeguard 3 and 4 show it.

### 3.2 New kind `documentation`
`documentation` means project documentation that is not architecture: README, changelog, contributing guide, user guide.

Heuristic changes (versioned, `CLASSIFIER_VERSION` 1 is the first version with this kind):
* README, CHANGELOG, CONTRIBUTING, LICENSE, CODE_OF_CONDUCT file names give `documentation`.
* A document in `docs/`, `doc/`, `documentation/`, `wiki/` without an architecture signal gives `documentation`. Before, it gave `architecture-doc`. The verdict stays weak.
* Architecture and requirements signals still win (`architecture`, `design`, `sad`, `adr`, `c4`).

Places that must know the new kind:

| Place | Change |
|---|---|
| `ingest.py` (`Kind`), CLI `KINDS` | add `documentation` |
| `contracts/common.schema.json`, `contracts/kg/schema.yaml`, OpenAPI enums | add to the kind enum |
| `stub.py` `LABEL_BY_KIND`, `builtin.py` | label `TextUnit`, same extractor as `architecture-doc` (sections, sentences) |
| `config/inconsistency/plugins.yaml`, plugin `match.kinds` | review: add where `architecture-doc` is listed and the plugin also fits documentation |
| `config/orchestrator/rules.yaml` `kindPairs` | no new pair (Q9). No TLR approach supports `documentation` to `code` yet, so the pair stays inactive. The user adds the pair when an approach exists. |
| `KindPair.java` (`KINDS` list in the TLR approach SPI) | add `documentation`. A test compares this list with the enum in `common.schema.json` and with `KINDS` in Python. |
| `services/query-service/.../console.py`, `upload.html` (web upload console) | It has its own `KINDS` and rule parser. It takes the kind list from the shared module. It sends rules only, so the server classifies as for the CLI. |
| `scripts/t24_guided.py`, `scripts/perf.py` | no change: they pass explicit rules. |
| Desktop and dashboard kind colors and filters | add one entry |

Warning: the `docs/` change alters existing projects. A file in `docs/` that was `architecture-doc` becomes `documentation` on the next ingestion. The server re-activates the artifact, and the TLR may drop links that came from that file.

Decision (Q8): `docs/` gives `documentation`. Existing projects are protected like this:

| Step | Detail |
|---|---|
| Audit (done, repository) | `tests/e2e/fixtures/mini/s1` to `s5` and `scripts/perf.py` already pin `docs/** = architecture-doc`. They do not change. The two `experiments/live/*/perturbed-input/sandman.yaml` files have no `docs/` rule and no `docs/` folder in the input. They do not change. Tests that rely on the old guess (`services/extraction-agent/tests/test_classify.py`, `test_extraction.py`) get new expected values in WP A. |
| Legacy function | The old heuristic stays for one release as `legacy_guess` (about 20 lines, marked for removal). |
| Detect | A file whose kind differs between `legacy_guess` and the new classifier gets reason `kind changed in classifier v1: was architecture-doc`. It is weak, so the review check (3.8) lists it. |
| Pin | `sandman classify DIR --pin-legacy` writes pin rules for those files into the managed block. The next ingestion then gives the same kinds as before. The user removes a pin to accept `documentation`. |
| Real projects in the graph | The repository does not contain them. The user runs `sandman classify DIR --check` once per project. Release note in `ingesting-artifacts.md`. |

### 3.3 Ignored files
Add a pseudo-kind `ignored`. It is never uploaded and is not in the manifest.
* Inside a git work tree: use `git ls-files` (respects `.gitignore`).
* Always also skip `node_modules`, `target`, `build`, `dist`, `out`, `venv`, `__pycache__`, `vendor`.
* The user overrides with a rule `glob=ignored` or `glob=code`.

### 3.4 Folder tree and folding
Build a tree from the file verdicts (module `sandman_agent.classify_tree`, so every client reuses it).

| Folder state | Display |
|---|---|
| All files below have the same kind (strict, all descendants) | One line: `src/main/ (code, 214 files)`. Collapsed. |
| Mixed kinds | Expanded one level. Each child is a folder line or a file line. |
| Contains a weak verdict | Marked `?`. Sorted to the top of its level. |

The user can open any collapsed folder. Opening shows the next level with the same rule.

### 3.5 From decisions to rules
The proposal is a list of rules. Rules are first-match-wins, so the generator writes:
1. File-level exceptions first (`docs/changelog.md=documentation`).
2. Folder-level rules next, deepest folder first (`docs/legacy/**=other`).
3. No rule for a file or folder when the heuristic already gives the result the user wants.

A correction on a folder sets one folder rule. A correction on a file sets one file rule. The generator removes rules that a later rule makes redundant. Property check: applying the rules gives the user's final kind for every file.

### 3.6 The managed block in `sandman.yaml`
The tool writes only inside a marked block. Everything outside is never touched. The tool edits text between the markers. It does not parse and re-dump the YAML, so comments and layout outside the block stay byte-identical.

```yaml
# Your own settings. SANDMAN never changes lines outside the managed block.
kindRules:                      # hand-written rules: checked first
  - { glob: "legacy/**", kind: ignored }

# >>> sandman:classification >>> BEGIN MANAGED BLOCK
# Warning: `sandman classify --write` and the dashboard and desktop review overwrite
# everything between BEGIN and END. Do not edit here. Put manual rules in kindRules above.
# checksum: 3f9a1c0e   reviewed: 9b21d4aa
reviewedRules:
  - { glob: "docs/changelog.md", kind: documentation }
  - { glob: "docs/legacy/**",    kind: other }
# <<< sandman:classification <<< END MANAGED BLOCK
```

Rules for the block:

| Rule | Detail |
|---|---|
| Two keys | `kindRules` is hand-written. `reviewedRules` is generated. The block never contains `kindRules`, so no duplicate YAML key. |
| Precedence | `--kind-rule`, then `kindRules`, then `reviewedRules`, then the ADR default rules, then the heuristic. The tool warns if a hand-written rule shadows a generated rule. |
| First write | No block yet: the tool appends the block at the end of the file (or creates the file). It shows the diff and asks first. |
| Later write | The tool replaces only the text between the markers. |
| Hand edit inside the block | `checksum` is the hash of the rules lines. If it differs, the tool says "the managed block was edited by hand, your edit will be overwritten", shows the diff, and asks. `--force` skips the question. |
| Broken markers | Only one marker, a duplicate block or wrong order: the tool refuses to write and names the line numbers. |
| `reviewed` | A fingerprint of the set of weak verdict paths at review time. `ingest` uses it (section 3.8). |
| Write | Atomic: write a temp file in the same folder, then rename. Line endings of the file are kept. |
| Read | The CLI reads `kindRules` and `reviewedRules` as ordinary YAML. The block is a normal comment structure, so any YAML reader works. |

All block logic (find markers, checksum, replace, diff) lives once in `sandman_agent.classify_tree`. The golden test file `contracts/classification/block-cases.json` covers: no file, file without block, file with block, edited block, broken markers, CRLF file.

### 3.7 Feature parity
Corrections work in the CLI, the desktop app and the dashboard. The dashboard is a server, and Q5 forbids server-side storage. So the dashboard produces the new file text and the user stores it.

| Feature | CLI | Desktop | Dashboard |
|---|---|---|---|
| Folded tree, open and close | yes | yes | yes |
| Kind, format, source, reason, weak marker | yes | yes | yes (from the graph) |
| Filter: needs a look, changed by me, by kind | yes | yes | yes |
| Search | `--files` + grep | yes | yes |
| Change kind of a file or folder | `-i` prompts | dropdown | dropdown |
| Live preview of the managed block and diff | yes | yes | yes |
| Save to `sandman.yaml` | `--write` | button (via CLI) | **download** or **copy**, and direct write in browsers with File System Access (stage 2) |
| Start the ingestion after the review | `ingest --review` | button | no (dashboard stays read-only for ingestion) |
| Data source | local directory | local directory | graph: last ingestion, current artifacts |

The dashboard difference: it shows the files of the last ingestion, not the local directory. A correction made there applies to the next ingestion of the same project.

Dashboard edit flow: the page keeps edits in the browser, sends them to `POST /api/classification/rules` (admin session, CSRF token like the existing replay and cancel actions). The endpoint is stateless. It calls the shared module and returns the block text, the diff and, if the user pasted the current `sandman.yaml`, the complete new file text. Nothing is stored. The JavaScript contains no classification or block logic.

### 3.8 Review check before ingestion (Q4)
`ingest` computes the weak verdicts of the directory and compares their fingerprint with `reviewed` in the managed block.

| State | Behavior |
|---|---|
| No weak verdicts | Prints the folded summary. Does not ask. |
| Weak verdicts and fingerprint matches | Reviewed. Does not ask. |
| Weak verdicts and no block, or fingerprint differs (new weak files) | Prints the warning, then asks: `[r]eview now  [p]roceed anyway  [a]bort`. |
| No TTY | Exits with code 2 and the warning. `--no-review` proceeds. |

The desktop app shows the same choice in a dialog when the user presses Ingest. The question always names the count: "11 files in 3 folders were not reviewed."

### 3.9 Graph storage (Q2)
Extend `Artifact` with `kindSource`, `kindReason`, `kindWeak`, `classifierVersion`. The extraction agent writes them in the ingestion transaction from the same `Verdict` it uses for `kind`.

* `contracts/kg/schema.yaml` and the ingestion contract get the four properties. Existing artifacts have none: readers treat absent as unknown, and the next ingestion fills them (a re-ingest of an unchanged file still updates the properties, no new version).
* The dashboard Cypher reads `a.kind, a.format, a.kindSource, a.kindReason, a.kindWeak`. Query-service and trace viewer ignore them.
* The ingestion result gets `kinds` (count per kind), used by safeguard 4.

## 4. CLI

### 4.1 New command `sandman classify DIR`
| Flag | Meaning |
|---|---|
| (none) | Print the folded tree. Exit 0. |
| `--open PATH` | Expand one folder (repeatable). `--all` expands everything. |
| `--files` | Flat list: `path  kind  format  source  reason`. |
| `--json` | Machine output of the folded tree and the files. The desktop app uses it. |
| `-i`, `--interactive` | Prompt loop (4.3). Needs a TTY. |
| `--write` | Write the managed block (section 3.6): diff, question, atomic write. |
| `--rules-stdin` | Read corrections as JSON from stdin, then act like `--write`. The desktop app uses it. |
| `--check` | Exit 1 if a weak verdict is not reviewed. For CI. Also lists files whose kind changed in classifier v1. |
| `--pin-legacy` | Write pin rules for files whose kind changed in classifier v1 (section 3.2). |
| `--force` | Skip the "edited block" question. |

`sandman ingest` prints the folded summary instead of one count line, runs the check of section 3.8, and accepts `--review` (start the loop first) and `--no-review`.

### 4.2 Mockup: default output
```
$ sandman classify ./media-store
media-store  412 files   5 ignored (node_modules/, target/)   classifier v1

  KIND                 PATH                          FILES  SOURCE
  requirements         requirements/                    3   heuristic  path segment 'requirements'
  architecture-doc     docs/architecture/               7   heuristic  path segment 'architecture'
  documentation     ?  docs/                            4   heuristic  weak: 'docs' directory
  architecture-model   model/                           2   heuristic  suffix .repository, .uml
  code                 src/main/                      214   heuristic  suffix .java
  test                 src/test/                       96   heuristic  path segment 'test'
  other             ?  scripts/                        11   default    no signal
  mixed                tools/                          9   2 kinds
    code                 tools/gen/                     6
    documentation        tools/README.md                1

Review: not done (3 folders with '?').
Run: sandman classify ./media-store -i      Expand a folder: --open docs/
```

### 4.3 Mockup: interactive loop
```
$ sandman classify ./media-store -i
[1/3] docs/   4 files   proposed: documentation   (weak: 'docs' directory)
      docs/overview.md   docs/api.md   docs/changelog.md   docs/legacy/old-notes.md
  [k]eep  [c]hange  [o]pen  [s]kip  [q]uit and save > o

   1 docs/overview.md          documentation  heuristic
   2 docs/api.md               documentation  heuristic
   3 docs/changelog.md         documentation  heuristic
   4 docs/legacy/old-notes.md  documentation  heuristic
  Number(s) to change, [b]ack > 1
  New kind: requirements | architecture-doc | architecture-model | documentation | code | test | other | ignored
  > architecture-doc

Changes to ./media-store/sandman.yaml (managed block only):
  + - { glob: "docs/overview.md", kind: architecture-doc }
  Lines outside the block stay unchanged.
  Caution: the lines between BEGIN and END MANAGED BLOCK are overwritten on every save.
Write? [Y/n] y
```

## 5. Desktop app
New tab **Classification** after **Ingest**. Ingest also shows a short summary with a link to the tab. When the user presses Ingest and the review check (3.8) fails, a dialog offers **Review now**, **Ingest anyway**, **Cancel**.

Data: `sandman classify DIR --json` through the Rust side (the app already starts the CLI). Saving: `sandman classify DIR --rules-stdin`. No new `/v1` call, so `contract.test.ts` does not change. The TS code only renders and collects edits (`logic.ts` with tests for the render model and edit collection).

### Mockup
```
+-- SANDMAN --------------------------------------------------------------+
| Overview | Ingest | Classification | Trace links | Findings | ...       |
+-------------------------------------------------------------------------+
| Folder: /Users/me/media-store            [Pick folder]  [Rescan]        |
| 412 files, classifier v1.   Review: 3 folders open                      |
|                                                                         |
| Show: (o) All  ( ) Needs a look  ( ) Changed by me     [Search...      ]|
|                                                                         |
|  v  mixed   media-store/                                                |
|     >  [requirements v]         requirements/          3 files          |
|     >  [architecture-doc v]    docs/architecture/     7 files          |
|  ?  v  [documentation v]       docs/                  4 files           |
|           [architecture-doc v]* overview.md           you               |
|           [documentation v]     changelog.md          heuristic         |
|           [other v]*            legacy/               you     1 file    |
|     >  [architecture-model v]   model/                2 files           |
|     >  [code v]                 src/main/             214 files         |
|     >  [test v]                 src/test/             96 files          |
|  ?  >  [other v]                scripts/              11 files          |
|     >  [ignored v]              node_modules/         5 files           |
|                                                                         |
|  * changed by you        Hover a row: source and reason.                |
+-------------------------------------------------------------------------+
| Managed block in sandman.yaml   (Caution: overwritten on save)          |
|   reviewedRules:                                                        |
|     - { glob: "docs/overview.md", kind: architecture-doc }              |
|     - { glob: "docs/legacy/**",   kind: other }                         |
|   [View diff]                                                           |
|                    [Reset]  [Save]  [Save and go to Ingest >]           |
+-------------------------------------------------------------------------+
```
* `>` and `v` open and close a folder. A kind dropdown on a folder sets the kind of all files below it.
* The kind name is always visible next to the color (accessibility).
* Keys: arrows move, Right/Left open/close, Enter opens the kind menu, `/` focuses search.

## 6. Dashboard
Read page `GET /classification?project=<id>`, data `GET /api/classification?project=<id>`, edit helper `POST /api/classification/rules`. The read part runs one fixed Cypher statement anchored on `projectId = $p` over the current artifacts, like the trace viewer. The tree, the folding and the rule generation come from `sandman_agent.classify_tree` in the Python handler. `static/classification.js` (plain ES5, no build step) renders the JSON with native `<details>` and handles the dropdowns. CSP stays `default-src 'self'`.

### Mockup
```
+-- SANDMAN dashboard ------------------------------------------------------+
| Projects  Workflows  Graph  Trace viewer  Classification  ...             |
+---------------------------------------------------------------------------+
| Project [media-store v]   Last ingestion #4, 412 files, classifier v1     |
|                                                                           |
| code 214 | test 96 | architecture-doc 7 | documentation 4 | requirements 3|
| architecture-model 2 | other 11                                           |
| [==========================code=============][test====][..]                |
|                                                                           |
| Show [all kinds v] [ ] needs a look [ ] changed by me  Search [_________] |
|                                                                           |
|  > requirements/            requirements        3 files                   |
|  v docs/                 ?  mixed               11 files                  |
|      > architecture/        architecture-doc    7 files                   |
|        overview.md          [architecture-doc v]*  you                    |
|        changelog.md         [documentation v]   heuristic: 'docs' dir     |
|        > legacy/            [other v]*          you  1 file               |
|  > model/                   architecture-model  2 files                   |
|  > src/main/                code                214 files                 |
|  > scripts/              ?  other               11 files   default        |
|                                                                           |
| Managed block (Caution: replaces the block in your sandman.yaml)          |
|  Optional: paste your current sandman.yaml to get the complete new file.  |
|  +------------------------------------------------------+                 |
|  | reviewedRules:                                       |                 |
|  |   - { glob: "docs/overview.md", kind: architecture-doc } |             |
|  +------------------------------------------------------+                 |
|  [Show diff]  [Copy]  [Download sandman.yaml]  [Write to file...] (stage 2)|
+---------------------------------------------------------------------------+
```
"Write to file..." uses the File System Access API (Chrome, Edge). Other browsers use Copy and Download. The browser only reads and writes the bytes. The Python endpoint builds the text.

## 7. Verification (success criteria)
| Step | Check |
|---|---|
| Move classifier, add `Verdict`, `documentation`, `ignored` | Existing extraction-agent tests pass (expected-value updates only for the `docs/` change). `cases.json` golden test runs in the library, the CLI and the extraction agent. |
| Version rule | Test fails if golden outputs change and `CLASSIFIER_VERSION` stays. |
| Ignore logic | Git fixture and non-git fixture. `node_modules` is never in the manifest. A `glob=code` rule overrides it. |
| Tree and folding | Uniform folder collapses. Mixed folder expands. A weak verdict propagates up as `?`. Rules are deepest-first. Property check for the rule generator. |
| Managed block | `block-cases.json`: all cases of 3.6. Text outside the block is byte-identical before and after (test with comments, CRLF, anchors). Edited block asks. Broken markers refuse. Atomic write leaves no temp file. |
| `sandman classify` | Tests on `tests/e2e/fixtures/mini`: output, `--json`, `--check` exit code, `--write`, scripted `-i`. |
| Review check | Four states of 3.8, TTY and no TTY, `--no-review`. |
| Contract and graph | `scripts/validate_contracts.py`. Ingestion writes `kindSource`, `kindReason`, `kindWeak`, `classifierVersion`. Result has `kinds`. Version mismatch gives a warning. |
| Uniformity (e2e) | Preview kinds equal graph kinds, file by file, on two fixtures (with and without rules). |
| Re-classification (e2e) | Ingest, change one rule, ingest again. The artifact has the new kind and TLR kind pairs follow. |
| Desktop | `npm test`, `npm run typecheck`, `cargo test` (stdin passing). |
| Dashboard | `tests/test_pages.py`: page for admin, `303` without session, JSON shape, CSP header, POST needs CSRF token, every statement has `{projectId: $p}`, endpoint stores nothing. |
| Docs | `ingesting-artifacts.md` (new kind, block, review), `desktop-app.md`, `operations.md`, CLI help, graphify update. |

## 8. Work packages and order
Each package is one commit on `develop`. Tests are green per step. Sonnet agents implement (see `CLAUDE.local.md`) and keep `.work/T-classification-review.md`.

| WP | Content | Depends on |
|---|---|---|
| A | Shared classifier, `Verdict`, version, golden cases, `legacy_guess`, `documentation` and `ignored` kinds across contracts, agent, configs, `KindPair.java`, upload console, enum-equality test, new expected values in old tests | - |
| B | Graph and contract: `kindSource`, `kindReason`, `kindWeak`, `classifierVersion`, `kinds` in the result, version warning | A |
| C | `classify_tree`: tree, folding, rule generator, managed block read and write, golden block cases | A |
| D | CLI: `classify`, `ingest` summary, review check, `--review`, `--no-review`, `--rules-stdin` | B, C |
| E | Dashboard: page, API, edit endpoint, JS (copy and download) | B, C |
| F | Desktop: tab, dialog, Rust stdin call | D |
| G | Dashboard stage 2: File System Access write | E |
| H | Docs and graphify | D, E, F |

A, B, C, D are the core. E and F are independent of each other. G is optional.

## 9. Decisions applied from review
| # | Decision |
|---|---|
| Q1 | Dashboard and desktop both support corrections, with feature parity where easy. Dashboard output is the new file text (copy, download, optional direct write), because it stores nothing (Q5). |
| Q2 | Source, reason, weak flag and classifier version are stored on `Artifact` in the graph. |
| Q3 | New kind `documentation`. |
| Q4 | `ingest` warns and asks if the review step is missing (section 3.8). |
| Q5 | Corrections live in `sandman.yaml` only, inside a marked managed block. |
| Q6 | Folding is strict. |
| Q7 | Ignore through `.gitignore` and through the fixed list. |

## 10. Decisions on the open points
| # | Decision |
|---|---|
| Q8 | `docs/` gives `documentation`. Existing projects are protected with detection and pin rules (3.2). The repository fixtures are already pinned. |
| Q9 | The pair `documentation` to `code` stays inactive. No TLR approach supports it yet. `documentation` is added to `KindPair.java` `KINDS` (enum equality), but not to `rules.yaml`. |
| Q10 | Accept: dashboard direct write works only in Chromium browsers. Copy and download work everywhere. |
| Q11 | A classifier version mismatch warns and never rejects. |

## 11. Risks
* Drift: covered by the six safeguards in 3.1.
* Managed block: text edits can corrupt a file. Covered by the marker rules, the byte-identical test, atomic write, diff and question.
* Large repositories (20 000 files, the manifest limit): folding keeps the visible list small. Desktop and dashboard render children on open only.
* `documentation` touches many files (3.2). WP A lists them. Missing one fails the contract validation or an enum test.
