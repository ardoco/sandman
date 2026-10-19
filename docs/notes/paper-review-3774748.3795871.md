# Paper review: AKM vision paper and Sandman coverage

Date: 2026-10-18. Status: done. Result: M6–M8 in `docs/stage-3-implementation-plan.md`, specs T6.1–T6.3.

## Source

Jan Keim, Angelika Kaplan. "From Scattered to Structured: A Vision for Automating Architectural Knowledge Management". ICSE-Companion '26, Rio de Janeiro, pp. 487–491. DOI 10.1145/3774748.3795871.

This note cites the paper as "Paper, §x, p. y". The PDF is not in the repository.

## Purpose

This note compares the paper vision with Sandman. It finds ideas that Sandman does not cover. It gives a recommendation for each idea. The plan then gets new tasks and milestones.

Rules for this review:
- Source code, contracts and tests are the reference for "implemented".
- A task counts as "done" if a commit closes it on `develop`.
- Effort classes are the plan classes: S ≤ 2 days, M 3–5 days, L 1–2 weeks.

## 1. Paper summary

The paper proposes an automated pipeline for architectural knowledge management (AKM). It has five parts (Paper, §3 and Fig. 1, p. 489):

| Part | Paper section | Content |
|---|---|---|
| Knowledge extraction | §3.1, p. 489 | Extractors for text (NLP, LLM), code (static analysis) and diagrams. Users add knowledge manually, for example as structured JSON. |
| Knowledge base | §3.2, p. 489 | A schema that supports conformance checks, change impact analysis, question answering and trace link recovery (TLR). |
| Consistency checking | §3.3, p. 489 | Find contradictions in decisions, documented-versus-implemented mismatches and outdated information. Use rule-, learning- and LLM-based methods. Resolve issues semi-automatically with humans. |
| Agent monitoring | §3.4, p. 489 | An agent watches the file system and version control. It recovers trace links, rates severity, fixes minor issues and escalates critical ones. It asks targeted clarification questions. It asks for confirmation before high-impact changes to the knowledge base. |
| Question answering | §3.5, p. 489 | Retrieval-augmented question answering like HubLink. Evaluate accuracy and efficiency. |

Fig. 1 (p. 489) adds three cross-cutting roles. The user validates results and receives information. The LLM applies, supports and explains. The knowledge base receives updates.

§4 (p. 490) lists nine challenges:
1. Heterogeneous and multimodal data.
2. Scalability and context limits.
3. Explainability and trust (hallucinations, verification, formal reasoning).
4. Extraction errors versus genuine inconsistencies, and planned inconsistencies.
5. Human-AI collaboration and feedback integration (cognitive load).
6. Contextual learning and project adaptation (few-shot, feedback refinement).
7. Knowledge base performance and maintenance, plus privacy of meeting minutes and voice.
8. Temporal validity and system evolution (temporal knowledge base, time-aware queries, style migrations).
9. Security and access management (anonymization).

§3 (p. 488) also plans a literature review, a taxonomy and an empirical evaluation of each step.

## 2. Coverage table

Status values:
- **implemented**: code, contract and tests exist.
- **partly**: a part exists; the gap is named.
- **planned Tx**: an existing or new task covers the gap.
- **changed (ADR)**: Sandman covers the goal with a different design on purpose.
- **not covered**: no task. The recommendation is in §3.
- **rejected R-n**: Sandman does not do this (§5).

| # | Paper idea | Paper source | Sandman status | Evidence or task |
|---|---|---|---|---|
| 1 | Extract text documents (Markdown, PDF, DOCX) | §3.1, p. 489 | implemented | T1.1–T1.4 |
| 2 | Extract requirements | §3.1, p. 489 | implemented | T1.x, requirement TextUnits |
| 3 | Extract architecture models (PCM, UML) | §3.1, p. 489 | implemented | T1.x model extractors |
| 4 | Extract code by static analysis | §3.1, p. 489 | implemented | T2.6 (ArDoCo code model, `DEPENDS_ON`) |
| 5 | Parse diagram images | §3.1, p. 489; §4 ch. 1, p. 490 | not covered | backlog B-1 |
| 6 | Manual knowledge input (structured JSON) | §3.1, p. 489 | partly: config files and explicit references only | new T7.2 |
| 7 | Architectural decisions and rationale as knowledge | §1, p. 487; §3.3, p. 489 | not covered | new T7.1 |
| 8 | Knowledge base with a schema | §3.2, p. 489 | implemented | ADR-0005, ADR-0014, `contracts/kg/schema.yaml` |
| 9 | TLR documentation ↔ model ↔ code | §2.1, p. 488; §3.2, p. 489 | implemented | T1.3, T1.12, T2.1, T2.1b, T2.2 |
| 10 | TLR model ↔ code (SAM ↔ code) as default | §3.2, p. 489 | planned T2.7 | T2.7 (another agent writes the spec) |
| 11 | Conformance checks documentation ↔ model | §3.2–3.3, p. 489 | implemented | T3.1, T3.2 (`ardoco-id`, `artemis-id`) |
| 12 | Conformance checks model ↔ code and documentation ↔ code | §3.3, p. 489 | partly: stub detector only | new T7.3 (needs T2.7) |
| 13 | Change impact analysis | §3.2, p. 489 | not covered | new T6.3 |
| 14 | Contradictions between decisions | §3.3, p. 489 | not covered | backlog, after T7.1 |
| 15 | Outdated information | §3.3, p. 489; §4 ch. 8, p. 490 | partly: versions and re-detection | T6.3, T7.4 |
| 16 | Rule-based and LLM-based detection | §3.3, p. 489 | implemented (rule and heuristic); LLM doc–code planned | T3.1, T3.2; T7.3 |
| 17 | Learning-based detection | §3.3, p. 489 | not covered | backlog B-2 |
| 18 | Semi-automated resolution with humans | §3.3, p. 489 | partly: triage lifecycle, no suggestions | ADR-0007, T3.4; new T7.5 |
| 19 | Continuous monitoring agent | §3.4, p. 489 | changed (ADR-0006, Q-4) | CI snapshot ingestion (T5.1), rule orchestrator, incremental processing |
| 20 | Watch the file system | §3.4, p. 489 | rejected R-2 | A-1, Q-4 |
| 21 | Automatic TLR on change | §3.4, p. 489 | implemented | orchestrator rules, T5.1 |
| 22 | Severity rating | §3.4, p. 489 | implemented | `Inconsistency.severity` (info, warning, error) |
| 23 | Fix minor issues automatically | §3.4, p. 489 | rejected R-4 | no write path into project repositories |
| 24 | Escalate critical issues | §3.4, p. 489 | partly: severity filter and dashboard | §10.1 dashboard; T6.1, T6.2 |
| 25 | Targeted clarification questions | §3.4, p. 489 | not covered | new T7.5 |
| 26 | Confirmation before high-impact actions | §3.4, p. 489 | partly | prune `dryRun` (T5.2); planner approval gate (T6.4) |
| 27 | LLM-based orchestrating agent | §3.4, p. 489 | changed (ADR-0006) | rule orchestrator; optional planner T6.4 |
| 28 | Question answering over the knowledge base | §3.5, p. 489 | implemented | M4: T4.1–T4.7, ADR-0018 |
| 29 | HubLink as the QA method | §2.5, p. 488; §3.5, p. 489 | not covered (own strategies instead) | backlog B-6 |
| 30 | User validates results (Fig. 1) | Fig. 1, p. 489 | partly: inconsistency triage only | new T6.1 (trace-link review) |
| 31 | LLM explains results (Fig. 1) | Fig. 1, p. 489 | partly | T3.4 evidence, link `rationale`, T4.3 citations |
| 32 | Scalability and context limits | §4 ch. 2, p. 490 | partly | NFR-15 bound; F-1 in progress; backlog B-4 |
| 33 | Explainability, trust and verification | §4 ch. 3, p. 490 | partly | schema validation, provenance, replay; new T7.6; ontology B-3 |
| 34 | Extraction error versus genuine or planned inconsistency | §4 ch. 4, p. 490 | partly: REJECTED versus ACCEPTED only | T3.6; new T6.2 |
| 35 | Feedback integration and cognitive load | §4 ch. 5, p. 490 | partly | new T6.1, T7.5, T8.1 |
| 36 | Project adaptation (few-shot, feedback) | §4 ch. 6, p. 490 | partly: config and static few-shot | new T8.2 |
| 37 | Knowledge base performance and maintenance | §4 ch. 7, p. 490 | implemented | T5.2, T5.3, T5.4, ADR-0005 |
| 38 | Privacy and anonymization | §4 ch. 7 and 9, p. 490 | partly | ADR-0013, `local-only` policy, retention; anonymization with B-1 |
| 39 | Temporal validity and time-aware queries | §4 ch. 8, p. 490 | partly: versions, ingestion sequence, retention of 20 versions | new T7.4 |
| 40 | Element identity across style migrations | §4 ch. 8, p. 490 | not covered | backlog B-5 |
| 41 | Security and access management | §4 ch. 9, p. 490 | implemented | ADR-0013, ADR-0016, T0.9, T1.14, T4.4 |
| 42 | Literature review and taxonomy | §3, p. 488 | rejected R-1 | research work outside this repository |
| 43 | Empirical evaluation of each step | §3, p. 488 | partly: components evaluated, users not measured | T2.3, T2.4, T3.3, T3.6, T4.2; new T8.1 |
| 44 | Make knowledge accessible to all stakeholders | §1, p. 487 | not covered: not measured | new T8.1 |

Summary of 44 ideas: 13 implemented, 16 partly covered, 1 planned (T2.7), 2 changed by design, 9 not covered, 3 rejected.

## 3. Candidates

Each candidate fills a gap from §2. The recommendation is one of: **do now** (M6), **do next** (M7, M8), **backlog**, **reject**.

### 3.1 Trace-link review with feedback carry-over (T6.1)

- What: Users confirm or reject trace links in the console. A review survives a new artifact version if both linked elements are unchanged.
- Why: Fig. 1 (p. 489) puts user validation in the loop. §4 ch. 5 (p. 490) asks for feedback integration. The contract `POST /v1/links/{linkId}/review` exists but returns 501. Detectors already ignore rejected links, so a review changes detection results at once.
- Effort: M.
- Dependencies: M2, T0.9.
- Recommendation: do now.

### 3.2 Triage cause categories (T6.2)

- What: Each triage transition to ACCEPTED, REJECTED or DEFERRED gets a cause. Causes separate extraction errors, link errors, detector errors, genuine inconsistencies, planned changes and intentional deviations.
- Why: §4 ch. 4 (p. 490) names the problem to tell "extraction errors" from genuine inconsistencies. It also names planned inconsistencies. Today `reason` is free text, so analysis needs manual coding.
- Effort: S.
- Dependencies: T0.7, T3.4.
- Recommendation: do now.

### 3.3 Change impact report (T6.3)

- What: For each ingestion, Sandman lists the changed elements. It follows trace links and dependencies. It lists elements in other artifacts that need a review, for example documentation sections.
- Why: §3.2 (p. 489) names change impact analysis as a use of the knowledge base. §3.3 names outdated information. The graph already holds the data. No LLM is necessary.
- Effort: M.
- Dependencies: M2, T2.6, T5.1.
- Recommendation: do now.

### 3.4 LLM planner with approval gate (T6.4)

- What: The existing optional M6 item. ADR-0006 describes it.
- Why: §3.4 (p. 489) describes an agent that plans and asks for confirmation before high-impact actions.
- Effort: L.
- Dependencies: M5.
- Recommendation: keep optional. Build it only if the rule orchestrator blocks a real use case.

### 3.5 Architectural decision extraction (T7.1)

- What: Extract decision records (ADR and MADR files) into `Decision` elements. Link decisions to model elements and code. The schema change is additive.
- Why: The paper names decisions and rationale as core knowledge (§1, p. 487; §3.3, p. 489). Sandman has no decision element type.
- Effort: M.
- Dependencies: M1, M2.
- Recommendation: do next. Contradictions between decisions stay in the backlog until T7.1 has data.

### 3.6 User-declared knowledge file (T7.2)

- What: A file `sandman-knowledge.yaml` in the project repository. It declares elements, aliases and trace links. Its links use the approach `user-declared`.
- Why: §3.1 (p. 489) asks for manual augmentation in a structured format. Today users can only add explicit references in documents.
- Effort: M.
- Dependencies: M1, M2.
- Recommendation: do next.

### 3.7 Model–code and documentation–code inconsistency detector (T7.3)

- What: A detector for model elements without code and code without model elements. A later step adds LLM-based documentation–code contradictions (Q-6). Evaluation uses a removal protocol like T3.3.
- Why: §3.3 (p. 489) names documented-versus-implemented mismatches. Sandman detects only documentation–model inconsistencies today.
- Effort: M.
- Dependencies: T2.7, T3.1.
- Recommendation: do next.

### 3.8 Time-aware queries (T7.4)

- What: Query the knowledge base "as of" an ingestion or a source revision. Strategies of ADR-0018 get an optional `asOf` parameter.
- Why: §4 ch. 8 (p. 490) asks for time-aware queries. Sandman keeps versions, but queries see only current versions.
- Effort: M.
- Dependencies: M4, T5.2.
- Recommendation: do next. Retention (20 versions) limits the history.

### 3.9 Resolution suggestions and clarification questions (T7.5)

- What: For an open inconsistency, an LLM proposes a resolution or one clarification question. The output is schema-validated and recorded. Users accept or ignore it. It never changes project artifacts. It is off by default.
- Why: §3.3 and §3.4 (p. 489) ask for semi-automated resolution and targeted questions.
- Effort: M.
- Dependencies: T3.4, T6.2.
- Recommendation: do next.

### 3.10 Grounding checks for LLM output (T7.6)

- What: Check that each LLM-produced fact cites existing knowledge base elements. Show agreement between TLR approaches as a trust signal.
- Why: §4 ch. 3 (p. 490) names hallucinations and verification.
- Effort: S.
- Dependencies: M4.
- Recommendation: do next.

### 3.11 Developer user study (T8.1)

- What: A study with developers. It measures usefulness, trust, task time and cognitive load. A protocol (`PROTOCOL.md`) is registered before the study.
- Why: §1 (p. 487) promises accessible knowledge. §4 ch. 5 (p. 490) names cognitive load. Sandman evaluates components but not users.
- Effort: L.
- Dependencies: M6, T5.5.
- Recommendation: do next.

### 3.12 Feedback-driven adaptation (T8.2)

- What: An experiment that uses reviewed links as few-shot examples and calibrates thresholds per project. A hash of the feedback snapshot goes into `configHash`.
- Why: §4 ch. 6 (p. 490) asks for project adaptation by feedback.
- Effort: L.
- Dependencies: T6.1.
- Recommendation: do next, as an experiment. It is off by default (RK-25).

## 4. Backlog

| Id | Item | Paper source | Reason for backlog |
|---|---|---|---|
| B-1 | Multimodal sources (diagram images, meeting minutes, recordings) with anonymization | §3.1, p. 489; §4 ch. 1, 7, p. 490 | Large. Needs a privacy design first. |
| B-2 | Learning-based inconsistency detection | §3.3, p. 489 | Needs labeled data. T3.6 and T6.2 produce it. |
| B-3 | Ontology and formal reasoning | §2.3, p. 488; §4 ch. 3, p. 490 | No use case in Sandman yet. |
| B-4 | Scale beyond NFR-15 | §4 ch. 2, p. 490 | NFR-15 is a deliberate bound. F-1 fixes the current budget miss. |
| B-5 | Element identity across renames and style migrations | §4 ch. 8, p. 490 | Stage 1 §4.2 defers rename detection. |
| B-6 | HubLink-style strategy and comparison with ADR-0018 strategies | §2.5, p. 488; §3.5, p. 489 | Research comparison. M4 strategies work. |

## 5. Rejected

| Id | Item | Paper source | Reason |
|---|---|---|---|
| R-1 | Literature review and taxonomy | §3, p. 488 | Research work. It does not belong in this repository. |
| R-2 | File system watching | §3.4, p. 489 | Sandman ingests snapshots from CI (A-1, Q-4). Watching adds state and access to developer machines. |
| R-3 | LLM orchestrator agent as the default | §3.4, p. 489 | ADR-0006 chose a rule orchestrator for reproducibility. T6.4 keeps an optional planner. |
| R-4 | Autonomous fixes of project artifacts | §3.4, p. 489 | Sandman has no write path into project repositories. The only automatic resolution is "not detected again". |

## 6. Decisions taken

The task gave no answer to these questions. This review uses the defaults below.

| Id | Question | Default |
|---|---|---|
| PR-D1 | Keep or replace the existing M6 (planner, link review)? | Keep M6. Rename it "Validate and inform". Link review becomes T6.1. The planner becomes T6.4 and stays optional. |
| PR-D2 | How many new milestones? | Three: M6 Validate and inform, M7 Knowledge depth, M8 Evaluation with people. |
| PR-D3 | Which tasks get a spec now? | T6.1, T6.2, T6.3. M7 and M8 tasks get specs when M6 is done. |
| PR-D4 | Does a link review apply to one link or to the element pair? | To the element pair. All active links between the same two elements get the review, across approaches. |
| PR-D5 | Does a review survive a new version? | Yes, if both elements have the same key and the same `contentHash`. |
| PR-D6 | Is the triage cause required? | Required in the console. Optional in the API, for compatibility. |
| PR-D7 | Does the impact report use an LLM? | No. It is a read-only graph traversal. |
| PR-D8 | Does any new feature write into project repositories? | No (R-4). |
| PR-D9 | Are LLM suggestions and adaptation on by default? | No. Both are off by default (RK-25, RK-26). |
| PR-D10 | Does M6 wait for the T5.5 soak test? | No. M6 can run in parallel with the soak period. M8 waits for T5.5. |

## 7. New risks

| Id | Risk | Mitigation |
|---|---|---|
| RK-25 | Feedback-driven adaptation breaks reproducibility. | Put a feedback snapshot hash into `configHash` and run manifests. Keep adaptation off by default. |
| RK-26 | Suggestions and questions add LLM cost and user load. | Off by default. Only for severity warning or higher. Budget stop applies. T8.1 measures the load. |
| RK-27 | The user study finds too few participants, or ethics approval is late. | Check consent and ethics early. Run a pilot first. |
