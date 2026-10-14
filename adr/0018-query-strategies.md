---
status: accepted
date: 2026-10-14
---

# Query strategies and automatic strategy selection

## Context and Problem Statement

ADR-0009 chose schema-grounded NL-to-Cypher and kept GraphRAG (E) and an agentic loop (C) as later extensions. Questions differ: structural ones ("which requirements have no trace link?") suit Cypher; fuzzy content ones ("where is payment handled?") suit retrieval; multi-hop ones ("how does X depend on Y?") suit graph exploration. How does the query service offer several answer strategies, let users pick one, and choose automatically?

## Decision Drivers

* Each strategy fits a different question type; one pipeline covers none of them well.
* Same safety rules for all strategies: read-only, one project, bounded cost (ADR-0009 amendment D-2).
* Users keep control; automatic choice must be visible and overridable.
* Evaluable per strategy and for the choice itself.

## Considered Options

* A: One strategy per endpoint.
* B: One endpoint `POST /v1/query/nl` with `strategy: auto | cypher | graphrag | explore`; `auto` classifies first.
* C: Always run all strategies and merge.

## Decision Outcome

Chosen option: "B".

| Strategy | Mechanism | Fits |
|---|---|---|
| `cypher` | ADR-0009 pipeline (generation, scope guard, read transaction, repair) | counts, filters, lists, structural gaps |
| `graphrag` | Vector search over element text (Memgraph vector index, filtered by `projectId`), then bounded graph expansion by fixed parameterized Cypher templates (neighbours, trace links, containing section/component) | fuzzy content questions |
| `explore` | Bounded tool loop (max steps, token and time budget). Tools: `get_schema`, `find_elements` (vector or key/name lookup), `neighbors`, `run_cypher`. Every tool call passes the scope guard and runs in a read transaction | multi-hop and "how is X connected to Y" |
| `auto` | One cheap structured-output LLM call returns `{strategy, confidence, reason}`; confidence below the threshold selects `cypher` | default |

Rules for all strategies:

* **Same guard.** Every Cypher statement, including generated templates and tool calls, goes through the parse-tree scope guard, the read transaction, the memory limit and the result post-check. Vector search carries the `projectId` filter and is post-checked the same way.
* **Same synthesis.** Answer synthesis has no tools and may cite only keys present in the rows or in `evidence` (R-032).
* **Visible choice.** The response carries the resolved `strategy`, the `classification` (for `auto`), `evidence` and, for `explore`, `steps`. The console shows them and lets the user re-run with another strategy.
* **No cascade in v1.** If the chosen strategy fails, the response reports it. Fallback between strategies is added only if evaluation shows a need.
* **Availability.** `config/query/strategies.yaml` enables strategies and sets budgets. A disabled or unimplemented strategy answers 501. `auto` considers only enabled strategies; with one enabled, no classifier call is made.
* **Embeddings.** `graphrag` and `explore.find_elements` need element embeddings. The extraction-agent owns them (single-writer rule): property `Element.embedding` plus the embedding model id, created via the gateway, vector index in `init.cypher`. Contract change to `contracts/kg/schema.yaml` is additive.

### Consequences

* Good, because each question type gets a fitting method, and `auto` hides the choice from casual users.
* Good, because the `strategy` field is additive; M0 already accepts it.
* Good, because the existing `mode` field (`answer | cypher-only`) keeps its meaning.
* Bad, because the classifier is a new error source; its accuracy is measured and a wrong choice is visible and overridable.
* Bad, because `explore` has the highest cost and the weakest determinism; it is off by default.
* Bad, because `graphrag` adds an embedding pipeline and a KG schema change.

### Confirmation

* Example set (`config/query/examples.yaml`) carries a `strategy` label per question. Report execution accuracy for `cypher`, answer quality (citation-grounded, judged against gold element keys) for `graphrag` and `explore`, and classifier accuracy and confusion matrix for `auto`.
* Security suite runs against all strategies: cross-project retrieval attempts, prompt-injection text in elements that tries to steer `explore` tool calls or the classifier, budget exhaustion.
* To verify: Memgraph vector index syntax, filtered search and memory cost (V-43).

## Pros and Cons of the Options

### A: One strategy per endpoint

* Good, because simple contracts.
* Bad, because every client must choose or duplicate classification; no `auto`.

### B: One endpoint with `strategy`

* Good, because one entry point for users, CLI and agents; additive.
* Bad, because the response is a union of strategy-specific fields.

### C: Run all and merge

* Good, because no classifier.
* Bad, because three times the cost, merging answers is unreliable.

## More Information

* Related: ADR-0009 (Cypher strategy and guard), ADR-0013 (roles), ADR-0004 (gateway).
