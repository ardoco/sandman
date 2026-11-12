# Spike T3.9: programming-language and architectural-style checks

Status: source analysis plus one live run on `sandman-example` (TeaStore) on 2026-11-12. Route: `local-only` policy, so the chat route is `ollama/qwen3:4b` on the remote Ollama.

## Result

| Item | Result |
|------|--------|
| Code input (decision 4) | A directory. The detector copies the current code files from the artifact store into a temporary directory. |
| Tools | `CodeDirectoryReadOnlyTools` runs on that directory. The ACM variant offers fewer tools and needs a code model service. |
| LLM injection (decision 3) | Build the NER and validation agents from public classes. The chat model is `ArtemisChatModel` over the gateway client. |
| Tool calls over the gateway | `LlmClient.chatWithTools` sends OpenAI `tools` and returns `tool_calls`. `ArtemisChatModel` maps them. Tested with a stub gateway. |
| Cost and time | Measured on TeaStore. `programming-language-id`: 247 s, `artemis-id`: 429 s, `architectural-style-id`: 51 s. Single gateway calls take 18 to 96 s. The model is local, so the money cost is zero. |
| Quality | TeaStore has no inconsistency gold standard. `programming-language-id` raised 3 findings (the first one: sentence says HTML, the code contradicts). `architectural-style-id` raised none. Not rated for precision. |
| Severity | The finding severity is `warning`. The task text says `medium`. The contract allows only `info`, `warning`, `error`, so `warning` stays. |

## Not used from upstream

* The architectural-style validator registry with specialised validators. The detector asks the LLM agent for every statement.
* `LargeLanguageModel`. It reads credentials from the environment and caches on disk.
* The ArDoCo pipeline stage. The detector calls `NamedEntityRecognizer` and the validation agent directly.

## Open

* Measure precision on the benchmark projects with an inconsistency gold standard (not vendored yet).
* Compare the directory input with the ACM input.
