# Authoring guide for the NL query evaluation set

This guide explains how to add question items to `dataset.yaml`.
The task spec is `docs/tasks/T4.2-nl-query-evaluation-set.md`.

## Rules for the test split

Warning: The test split must come from independent human authors.
PROTOCOL section 7 requires this.

- Write test items without access to the prompts, the intent file, or the dev items.
- Do not tune prompts or intents with test items.
- Use only the projects `teammates` and `jabref` for test items. No dev item uses these projects.
- Use query shapes that no dev item uses. The validator reports a shared template as leakage.
- Write the question in your own words. The validator reports near-duplicates of dev questions.
- Set `author: human:<your-id>` and `split: test`.
- Do not use `ai-draft` in the test split. The validator rejects it.

## Item fields

| Field | Meaning |
| --- | --- |
| `id` | Unique id. Use `nl-<project short>-<number>`. |
| `project` | Project id in the knowledge graph. |
| `question` | The natural-language question. |
| `strategy` | `cypher`, `graphrag` or `explore`. This is the label the classifier must predict. |
| `author` | `ai-draft` or `human:<id>`. |
| `split` | `dev` or `test`. |
| `cypher` | Gold Cypher. Required for `cypher` items. For `explore` items it is the reference query. |
| `gold_keys` | Element keys that a correct answer must cite. Required for `graphrag` and `explore` items. |

## Choose the strategy

| Strategy | Use when |
| --- | --- |
| `cypher` | The answer is a table or a number that a graph query computes. |
| `graphrag` | The answer needs text content. The gold keys are the supporting sentences. |
| `explore` | The answer needs a multi-hop walk over relations. The gold keys are the elements on the answer. |

## Write the gold Cypher

1. Anchor every `MATCH` with `{projectId: $projectId}`.
2. Do not use `LIMIT`. Execution accuracy compares full results.
3. Do not use `CALL` or write clauses.
4. Use `ORDER BY` only for readability. The comparison ignores row order.
5. Return only the columns that answer the question.
6. Make sure the result is not empty.

## Write gold keys

1. For a `graphrag` item, read the document and list every sentence that supports the answer.
2. For an `explore` item, write a reference query. Return the element key in the first column.
3. Use the key format `<artifact id>#<local key>`. The `query` command prints keys.

## Validate an item

Run the commands from the repository root. Docker must be available.

```
uv run python experiments/nlquery/seed_kg.py query --project mediastore "<cypher>"
uv run python experiments/nlquery/seed_kg.py gold
uv run sandman eval nlquery validate --dataset experiments/nlquery/dataset.yaml
```

The seed KG contains all five benchmark projects, including `teammates` and `jabref`.
Trace-link confidence, link status, inconsistencies and concepts in the seed KG are synthetic.

## Item template

```yaml
- id: nl-tm-001
  project: teammates
  question: <question in your own words>
  strategy: cypher
  author: human:<your-id>
  split: test
  cypher: |
    MATCH (c:ModelElement {projectId: $projectId, kind: 'component'})
    RETURN c.name AS name
```
