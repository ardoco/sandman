# Ingesting artifacts

An ingestion sends a snapshot of a project directory to SANDMAN. It needs the `maintainer` role (or an admin). The running example is the mini fixture in `tests/e2e/fixtures/mini`:

```
s1/
  sandman.yaml                      kind rules (not ingested)
  requirements/requirements.md      REQ-1, REQ-2, REQ-3 as headings
  docs/architecture.md              mentions MediaCache and CredentialStore
  docs/old-notes.md
  model/mini.repository             PCM repository, two components
  src/main/java/mini/MediaCache.java
  src/test/java/mini/MediaCacheTest.java   comment "// REQ-1"
s2/                                 s1 without docs/old-notes.md; REQ-2 renamed to "Upload audio into cache"
```

## 1. Prepare the directory

Every file gets a **kind** from the first matching rule; unmatched files are `other`. Rules live in `sandman.yaml` in the root of the directory (the file itself is not uploaded) and/or on the command line:

```yaml
kindRules:
  - { glob: "requirements/**", kind: requirements }
  - { glob: "docs/**", kind: architecture-doc }
  - { glob: "model/**", kind: architecture-model }
  - { glob: "src/test/**", kind: test }
  - { glob: "src/main/**", kind: code }
```

- Kinds: `requirements`, `architecture-doc`, `architecture-model`, `code`, `test`, `other`.
- Globs match the whole relative path with `/` separators; `**` spans directories.
- An entry may carry `format: ...` to override format detection (`markdown`, `text`, `csv`, `pcm-repository`, `uml`). Otherwise the format comes from the file suffix.
- `--kind-rule 'glob=kind'` can be repeated; command-line rules are checked before `sandman.yaml`. There is no format part on the command line.
- The CLI skips dot-files and dot-directories (`.git`, ...), symbolic links and `sandman.yaml`.

Pitfall: the kind of a path is fixed when the artifact is first created. Changing a rule later does not re-classify files that already exist in the project (changed content gets a new version, but the artifact keeps its kind). Decide the rules before the first real ingestion.

### Supported formats

| Format | Suffix | Kinds | Extracted |
|---|---|---|---|
| Markdown | `.md`, `.markdown` | any except `requirements` | a file element, one section element per heading (text before the first heading becomes `(preamble)`), one element per sentence, with line numbers |
| Plain text | `.txt` | any except `requirements` | as Markdown, but paragraphs only (no headings) |
| Requirements, Markdown | `.md`, `.markdown` | `requirements` | a file element and one `Requirement` per id heading |
| Requirements, CSV | `.csv` | `requirements` | a file element and one `Requirement` per row |
| PCM repository | `.repository` | `architecture-model` | components and interfaces, `PROVIDES`/`REQUIRES` relations |
| UML (XMI) | `.uml`, `.xmi` | `architecture-model` | components and interfaces, `PROVIDES`/`REQUIRES` relations |
| PDF, DOCX | `.pdf`, `.docx` | any except `requirements` | as Markdown (file, sections, sentences) via Docling, without line numbers; no OCR, so scanned documents yield little or no text |
| everything else | e.g. `.java` | any | one file-level element (name, first 500 characters of text) |

Code and test files get file-level elements from the built-in extractor. Richer code elements come from the `code-extractor` plugin, which the stub profile provides in the demo setup ([getting-started.md](getting-started.md), [plugins.md](../plugins.md)). PDF and DOCX are converted by the separate `document-converter` service (Docling in a resource-limited subprocess, no network access; the extraction agent calls it over the internal network). The converter's limits are 120 s, 300 pages, 50 MiB and 8 GiB address space per conversion, set by `SANDMAN_CONVERTER_*` variables of that service; `builtin.converter.timeoutSeconds` in `config/extraction/plugins.yaml` is the extraction agent's HTTP timeout. If a conversion fails, exceeds a limit or the converter is down, the document gets a file-level element and the extraction finishes with status `partial` and a warning.

### Requirements

Markdown: a heading (`#` to `######`, ATX style) whose first word is an id becomes a requirement. The id starts with a letter, ends with a digit and may contain letters, digits, `_`, `.` and `-` (`REQ-1`, `FR-12`, `NFR.3.1`). The rest of the heading is the title. The text runs from the heading to the next heading of any kind. Headings without an id (`# Requirements`), underlined headings and headings inside code fences are ignored.

```markdown
## REQ-1 Cache media files

The system shall cache media files to reduce load times.
```

CSV: UTF-8 with a header row. Columns `id` and `text` are required; `title` (default: the text) and `type` (default: `requirement`, becomes the element kind) are optional; column names are case-insensitive.

```csv
id,text,title,type
FR-1,The system shall log in users.,Login,functional
```

Duplicate ids are skipped with a warning, and so are CSV rows without an id. Markdown headings without an id are simply not requirements.

### Explicit references

Text in any ingested file that matches a pattern from `config/extraction/plugins.yaml` (`explicitReferences.patterns`, default `REQ-\d+` and `FR-\d+`) and equals the id of a requirement creates a trace link `explicit-reference` (confidence 1.0) from the requirement to the element containing the reference. In the mini example, `// REQ-1` in `MediaCacheTest.java` links REQ-1 to the test. Other id schemes need an additional pattern; the extraction agent reads the file at startup, so restart it afterwards. References are resolved when the referring file is extracted.

### Architecture models

- PCM: the root must be a `Repository`; `components__Repository` and `interfaces__Repository` become elements named by `entityName`, with relations from `providedRoles_*` and `requiredRoles_*`. Composite structure and signatures are not modelled.
- UML: the root must be a `Model`; only direct `packagedElement` children of type `uml:Component` and `uml:Interface` are read.

A model that does not match (other root, invalid XML, DTDs or entities) falls back to a file-level element and the run becomes `partial`. Relations to unknown interfaces are skipped with a warning.

## 2. Run the CLI

```sh
export SANDMAN_URL=http://127.0.0.1:8080     # default
export SANDMAN_TOKEN=sdm_...                 # or --token-file FILE
uv run sandman ingest --project demo --mode full --revision s1 tests/e2e/fixtures/mini/s1
```

| Option | Meaning |
|---|---|
| `--project` | project id (required) |
| `--mode full` / `partial` | required. `full`: the directory is the complete state, missing files are marked deleted. `partial`: only the listed files changed, nothing is deleted |
| `--revision` | free text, for example a Git commit; used by the question `what changed since <revision>` ([querying.md](querying.md)) |
| `--kind-rule glob=kind` | repeatable |
| `--token-file` | file with the token, instead of `$SANDMAN_TOKEN` |

The CLI hashes all files, asks the server which blobs it lacks, uploads only those as one `tar.gz`, and prints the ingestion as JSON on stdout (progress lines go to stderr):

```
6 files: architecture-doc=2, architecture-model=1, code=1, requirements=1, test=1
uploading 6 of 6 files
{ "ingestionId": "ing_...", "projectId": "demo", "seq": 1, "mode": "full", "sourceRevision": "s1",
  "createdVersionIds": [ ...6 ids... ], "reactivatedVersionIds": [], "deletedArtifactIds": [],
  "unchangedCount": 0, "createdAt": "..." }
```

Errors end the command with `sandman: <METHOD> <path>: <status> <body>`: `403` (role below maintainer), `404` (unknown project, or not visible to you), `400`/`422` (invalid manifest or archive).

Console alternative: **Projects**, then **Upload** (maintainers and admins). Choose a `tar.gz` or `zip` archive (paths relative to the project root), the mode, an optional revision, and kind rules one per line as `glob = kind[:format]` (the textarea is pre-filled with the default layout above). The result JSON is shown on the page.

### LLM enrichment

After extraction, requirements and document sections get a short `summary` and `Concept` nodes (`MENTIONS` edges with a confidence). This is a separate `Run` with the tool `llm-enrichment`. It uses `chat-default` for `cloud-allowed` projects and the self-hosted `chat-local` for `local-only` projects (see [getting-started.md](getting-started.md) for the models). Settings are in `config/extraction/enrichment.yaml`; `enabled: false` or the request option `llmEnrichment: false` switches it off.

Enrichment is optional: if the model is not reachable or the policy forbids the route, the extraction result stays and the result contains a warning. Responses that do not match the schema are retried once and then skipped for that element. An exhausted gateway budget blocks the workflow like any other budget stop. Unchanged elements of a new version reuse the earlier summary and concepts without a model call.

## 3. Versions, re-ingestion and deletions

- A file whose SHA-256 equals the current version is unchanged (`unchangedCount`); nothing happens.
- Changed content creates a new version that becomes current (`createdVersionIds`).
- Content that the project has had before becomes current again (`reactivatedVersionIds`), for example when you go back to an earlier snapshot.
- In `full` mode, a path missing from the snapshot is marked deleted (`deletedArtifactIds`) and has no current version until it is ingested again.
- Sending the same snapshot again changes nothing and starts no workflow, but still consumes an ingestion sequence number.

Try it: `--revision s2` with `tests/e2e/fixtures/mini/s2` creates one version (REQ-2 renamed) and deletes `docs/old-notes.md`. Ingesting `s1` again reactivates both.

A new or changed ingestion starts a workflow automatically; see [workflows.md](workflows.md).

## 4. Limits, warnings and partial runs

| Limit | Value |
|---|---|
| files per ingestion | 20 000 |
| archive size | 1 GiB (`SANDMAN_MAX_ARCHIVE_BYTES` of the extraction agent) |
| text file (Markdown, text, requirements) | 5 MiB |
| model file (XML) | 10 MiB |
| requirements per file | 10 000 (CSV: 100 000 rows) |
| element text | 4 000 characters (section and requirement), 500 (file) |
| warnings per file | 100, each cut to 300 characters |

Archives with links, special files, absolute paths, `..` segments or duplicate entries are rejected; a SHA-256 that does not match the manifest is rejected with `422`.

If an extractor fails on a file (too large, invalid XML, missing CSV columns), the file still gets its file-level element, the extraction run is recorded as `partial`, and the workflow carries on. Skipped requirements and similar notes become warnings. Today the warning texts are not shown in the console, the CLI or the workflow detail; what you can see is the run status:

```sh
curl -s -H "Authorization: Bearer $SANDMAN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"projectId":"demo","cypher":"MATCH (r:Run {projectId: $projectId}) RETURN r.skill, r.tool, r.status, r.error"}' \
  http://127.0.0.1:8080/v1/query/cypher
```

`partial` for `builtin-extractor` means at least one file fell back or an extractor failed; `failed` plugin runs carry the error in `r.error`. The extraction agent log names the file for a fallback (`built-in extractor failed`, see [operations.md](operations.md)).
