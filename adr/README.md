# Architecture Decision Records

This folder holds the architecture decisions of SANDMAN, one Markdown file per decision.

## Format

We use [MADR 4](https://adr.github.io/madr/) (Markdown Any Decision Records): YAML front matter with `status` and `date`, followed by context and problem statement, decision drivers, considered options, decision outcome (with consequences and confirmation), pros and cons of the options, and related ADRs. MADR is chosen because it is lightweight, Markdown-native, widely used and has explicit sections for drivers and options, which keeps the reasoning traceable for research.

Status values:

| Status | Meaning |
|---|---|
| `proposed` | Drafted; depends on an open question or a pending verification. |
| `accepted` | Decided; may still be revisited during review. |
| `deprecated` | No longer relevant, not replaced. |
| `superseded` | Replaced by a later ADR (link it as `superseded by ADR-NNNN`). |

## Adding an ADR

1. Copy [`template.md`](template.md) to `NNNN-short-title.md` using the next free number (numbers are never reused).
2. Fill in all sections; list only options that were actually considered.
3. Add a row to the index below.
4. Never rewrite an accepted decision; write a new ADR and mark the old one `superseded`. Exception before implementation starts (M0): decisions taken in a design review are added as dated entries in a final `Amendments` section of the affected ADR.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-language-per-component.md) | Implementation language per component | accepted |
| [0002](0002-use-a2a-for-agent-commands.md) | Use A2A for commands to agents | accepted |
| [0003](0003-nats-jetstream-event-bus.md) | NATS JetStream as event bus | accepted |
| [0004](0004-llm-gateway.md) | LLM gateway: Bifrost, LiteLLM as fallback | proposed |
| [0005](0005-kg-schema-provenance-versioning.md) | KG schema, provenance and artifact versioning | accepted |
| [0006](0006-rule-based-orchestration.md) | Rule-based orchestration with optional LLM planner | accepted |
| [0007](0007-inconsistency-lifecycle.md) | Inconsistency lifecycle and re-detection | accepted |
| [0008](0008-monorepo-layout.md) | Monorepo layout with shared contracts | accepted |
| [0009](0009-nl-to-cypher.md) | NL-to-Cypher approach | accepted |
| [0010](0010-minimal-agent-library.md) | Minimal agent library per language | accepted |
| [0011](0011-inconsistency-console.md) | User interface for inconsistencies | accepted |
| [0012](0012-pluggable-approaches-and-detectors.md) | Pluggable TLR approaches, extractors and detectors | accepted |
| [0013](0013-users-and-roles.md) | Users and roles | accepted |
| [0014](0014-graph-database.md) | Graph database: Memgraph Community | accepted |
| [0015](0015-ardoco-integration.md) | ArDoCo integration | proposed |
| [0016](0016-system-overview-dashboard.md) | System overview dashboard | accepted |
