# Files

- [SANDMAN CLI](cli.md) - The sandman CLI (tools/cli) ingests project snapshots into SANDMAN, lists and waits for workflows, and exports trace links or inconsistencies, all through the query-service API behind the edge proxy.
- [LLM Gateway (Bifrost)](llm-gateway.md) - The Bifrost llm-gateway as the only egress path for LLM calls — provider and per-component virtual-key configuration in deploy/gateway/config.json, cloud and Ollama-only keys, budgets and rate limits, the local Ollama profile, and how a project's llmPolicy selects keys and routes.
