# Derived architecture models

Goal: use the architecture models that Sandman derives from your architecture documentation.
A derived model lists the components that a language model finds in a document.
The derivation is optional. You can switch it off.

## When Sandman derives a model

1. Sandman looks at each architecture document of the project.
2. If the project has no architecture model of its own, Sandman asks a language model for the component names.
3. Sandman stores the names as a derived model. The model has the origin `derived`.

The LLM policy of the project decides which language model Sandman uses.

| Policy | Language model route |
|---|---|
| `cloud-allowed` | `chat-default` |
| `local-only` | `chat-local` |

If the route or its key is missing, Sandman skips the derivation and reports the warning `model derivation skipped: no LLM route for policy`.
Sandman never sends the text of a `local-only` project to a cloud route.

## See a derived model

1. Run `sandman artifacts list --project <id>` or query the artifacts of the project.
2. Find the artifacts with a path that starts with `.sandman/derived/exarch/`.
3. Read the file. Each line has the form `Name::Name`.

Each derived version points to its source document (`derivedFrom`) and to the run that made it (`derivationRunId`).
Elements of a derived model carry the flag `inferred`.

Note: You cannot upload files under `.sandman/`. Sandman rejects them with `INVALID_INPUT`.

## Switch off the derivation

1. Open `config/orchestrator/rules.yaml`.
2. Set `modelDerivation.enabled` to `false` to switch it off for all projects.
3. To switch it off for one project, add the project under `modelDerivation.projects`, with `enabled: false`.
4. Restart the orchestrator.

## Replace a derived model with your own

1. Upload your architecture model as an artifact of kind `architecture-model`.
2. Sandman marks all derived models of the project as deleted.
3. Trace link approaches use your model from the next workflow.

If you delete your model, the derived models return on the next derivation.

If you change or delete an architecture document, Sandman updates or removes the derived model of this document.
