# Declare knowledge in sandman-knowledge.yaml

Use the knowledge file to add facts that no artifact contains.
The file can declare elements, give elements more names, and state trace links.

## Place the file

1. Create the file `sandman-knowledge.yaml` in the root of the ingested directory.
2. Ingest the directory as usual (see [ingesting-artifacts.md](ingesting-artifacts.md)).

A project has at most one knowledge file.
The file is an artifact of kind `other` and format `sandman-knowledge`.
The kind rules do not apply to it.
If the file is invalid, the ingestion fails with status 400 and nothing is stored.
The error text is `INVALID_INPUT: sandman-knowledge.yaml: <JSON pointer>: <message>`.

## Write the file

```yaml
version: 1
elements:
  - id: payment-provider
    label: ModelElement
    kind: component
    name: External Payment Provider
    text: Third-party service that settles payments.
aliases:
  - element: model/mini.repository#_MediaCache
    names: [Media Store, MS]
  - element: decl:payment-provider
    names: [PSP]
links:
  - source: requirements/requirements.md#REQ-1
    target: src/main/java/mini/MediaCache.java#MediaCache
    type: implements
    rationale: The cache class implements the first requirement.
  - source: decl:payment-provider
    target: requirements/requirements.md#REQ-1
    type: relates
```

| Key | Content |
|---|---|
| `version` | Must be `1`. |
| `elements` | Elements to add. Each has `id`, `label`, `kind`, `name` and optional `text`. |
| `aliases` | More names for one element. Each has `element` and 1 to 20 `names`. |
| `links` | Trace links. Each has `source`, `target`, `type` and optional `rationale`. |

## Refer to elements

| Reference | Meaning |
|---|---|
| `decl:<id>` | An element declared in the same file. |
| `<path>#<localKey>` | An element of the artifact at `<path>` in the same project. |

The reference `<path>#<localKey>` is the element key without the artifact id.
Find the key of an element in the graph explorer or with a query.
If a reference matches no current element, the extraction run reports the warning `knowledge: unresolved <reference>`.
SANDMAN skips that link and still writes the other links.

## Understand the result

- Each link entry becomes a trace link with approach `user-declared`, status `confirmed` and confidence 1.0.
- Each alias entry adds a `relates` link from an alias element to the named element.
- Natural language queries find an element by an alias name through that link.
- Links follow the current version of both ends after each ingestion.
- If you delete the file, all `user-declared` links become inactive.
- You cannot review `user-declared` links. The review request answers 409. Change `sandman-knowledge.yaml` instead.

## Limits

| Item | Limit |
|---|---|
| File size | 1 MiB |
| Elements | 5000 |
| Aliases | 5000 |
| Links | 20000 |

Caution: YAML anchors and aliases are not allowed.
