# Spike T3.9: programming-language and architectural-style checks

Status: source analysis only. Not measured. No LLM was available.

## Result

| Item | Result |
|------|--------|
| Code input (decision 4) | A directory. The detector copies the current code files from the artifact store into a temporary directory. |
| Tools | `CodeDirectoryReadOnlyTools` runs on that directory. The ACM variant offers fewer tools and needs a code model service. |
| LLM injection (decision 3) | Build the NER and validation agents from public classes. The chat model is `ArtemisChatModel` over the gateway client. |
| Tool calls over the gateway | `LlmClient.chatWithTools` sends OpenAI `tools` and returns `tool_calls`. `ArtemisChatModel` maps them. Tested with a stub gateway. |
| Cost and time | Not measured. One NER call per document. One validation agent run per statement, with several tool round trips. |
| Quality | Not measured. Step 8 measures it. |

## Not used from upstream

* The architectural-style validator registry with specialised validators. The detector asks the LLM agent for every statement.
* `LargeLanguageModel`. It reads credentials from the environment and caches on disk.
* The ArDoCo pipeline stage. The detector calls `NamedEntityRecognizer` and the validation agent directly.

## Open

* Measure cost, time and precision on `sandman-example` and the benchmark projects.
* Compare the directory input with the ACM input.
