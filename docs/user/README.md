# SANDMAN user documentation

SANDMAN ingests a project snapshot (requirements, documentation, architecture models, code), extracts knowledge into a graph, derives trace links between artifacts, detects inconsistencies, and lets you query and triage the results. These pages describe how to work with a running system. They cover what the code does today (milestone M0 plus T1.1 to T1.3); later features are marked "planned".

Audience: operators who run the stack and project users (architects, researchers) who feed it and read the results. Developers of SANDMAN see the top-level [README](../../README.md); plugin authors see [plugins.md](../plugins.md).

## Pages

| Page | Read it to |
|---|---|
| [getting-started.md](getting-started.md) | install, generate secrets, start the stack, first login |
| [projects-and-access.md](projects-and-access.md) | users, roles, projects, personal API tokens |
| [ingesting-artifacts.md](ingesting-artifacts.md) | prepare a repository, run `sandman ingest`, understand versions, limits and partial runs |
| [workflows.md](workflows.md) | follow and trigger the processing that follows an ingestion |
| [querying.md](querying.md) | ask questions, run Cypher, call the REST API, export |
| [trace-links-and-findings.md](trace-links-and-findings.md) | read trace links and inconsistency findings, triage them |
| [operations.md](operations.md) | start, stop, logs, profiles, data, troubleshooting |
| [evaluation.md](evaluation.md) | run the Memgraph integration tests and the reproduction study (T2.4) |

## Interfaces

| What | Where |
|---|---|
| Console (web) | `http://127.0.0.1:8080/console` |
| REST API | `http://127.0.0.1:8080/v1/...` (bearer token) |
| CLI | `uv run sandman` (subcommands `ingest`, `workflows`, `export`) from a checkout |
| System overview (dashboard) | planned (T1.9), `http://127.0.0.1:8090` |

All user-facing traffic goes through one address, the edge proxy on `127.0.0.1:8080`. It is not reachable from other hosts unless you change `deploy/docker-compose.yml`.

## Concepts

- **Project**: the unit of access and analysis, identified by a slug (`^[a-z0-9][a-z0-9-]{1,62}$`, for example `demo`). It has a display name and an LLM policy (`cloud-allowed` or `local-only`). No built-in component calls an LLM today, so the policy has no visible effect yet.
- **Artifact**: one file of the project, identified by its relative path. It has a **kind** (`requirements`, `architecture-doc`, `architecture-model`, `code`, `test`, `other`) and optionally a format.
- **Version**: the immutable content of an artifact (identified by its SHA-256). An artifact has at most one current version. An artifact is deleted when a full snapshot no longer contains its path.
- **Ingestion**: one upload of a snapshot (`full`) or of changed files (`partial`), numbered per project, with an optional source revision such as a Git commit.
- **Element**: an extracted unit of a version: a requirement, a document section or sentence, a model component or interface, a code unit, a test. Its **key** is `art_<id>#<local key>`, for example `art_983d...#REQ-1`.
- **Trace link**: a typed, directed relation between two elements (`documents`, `implements`, `satisfies`, `verifies`, `relates`) produced by an **approach**, with a confidence and a status (`candidate`, `confirmed`, `rejected`).
- **Finding** (also called inconsistency): a potential problem found by a detector, with a severity (`info`, `warning`, `error`) and a triage status (`OPEN`, `DEFERRED`, `ACCEPTED`, `REJECTED`, `RESOLVED`, `REOPENED`).
- **Workflow**: the chain extraction, trace link recovery, detection that runs after an ingestion or on request. Each **step** has a status and attempts.
- **Run**: a provenance record of one tool invocation (extractor, approach, detector) with its status `completed`, `partial` or `failed`.

## What exists today

| Area | Today | Planned |
|---|---|---|
| Extraction | Markdown, plain text, requirements (Markdown/CSV), PCM repository, UML/XMI, file-level elements for everything else, optional LLM enrichment (summaries and concepts of requirements and document sections) | PDF and DOCX (T1.4) |
| Trace links | `explicit-reference` (built in), `stub-name-match` (token overlap, for pipeline checks), `ardoco` (document to model), `ardocode` (document to code) | further approaches such as LiSSA (disabled in `config/tlr/approaches.yaml`) |
| Findings | `stub-untraced` (requirement without active link) | further detectors |
| Natural language questions | five fixed question patterns, no LLM | LLM-based translation |
| Web UI | console (projects, findings, upload, workflows, ask, tokens, administration) | dashboard for admins (T1.9) |

The stub components exist to exercise the pipeline end to end. Treat their links and findings as demonstration data, not as analysis results.
