# Writing SANDMAN plugins

Plugins add extractors (e.g. code extraction) and inconsistency detectors without changing SANDMAN (ADR-0012). A plugin is an **external system**: any language, any internal design. It receives a request from the owning agent, returns results, and the owner validates and writes them with provenance. The reference implementation is `tests/plugin-stubs` (`sandman_plugin_stubs/app.py`). The shipped code extraction plugin is `services/code-extractor-ardoco` (see Reference plugins).

## Contracts

| Operation | Called by | Input / output (`contracts/skills.schema.json`) | Transport |
|---|---|---|---|
| extract | extraction-agent | `PluginExtractInput` → `PluginExtractOutput` | A2A skill `sandman.plugin.extract` |
| code-model (optional) | tlr-agent (approach `ardocode`, ArDoCo SAD to code) | `PluginCodeModelInput` → `PluginCodeModelOutput` | A2A skill `sandman.plugin.code-model` |
| detect | inconsistency-agent | `PluginDetectInput` → `PluginDetectOutput` (`Finding[]`) | plain HTTP `POST /v1/detect` |

Both transports are part of the contract with identical payloads, but in M0 each owner implements one of them (D-7): the extraction-agent calls extraction plugins via A2A only, the inconsistency-agent calls detector plugins via HTTP only.

**Extract.** Input lists the artifact versions to process (`path`, `kind`, `storagePath`, `contentHash`) and `context.currentArtifacts` (all current artifacts routed to the plugin, for cross-file relations). Output:
- `elements`: `path` + `localKey` identify an element. `localKey` must be unique within the artifact and stable across versions (e.g. qualified name + signature). `label` is one of `CodeUnit`, `TestCase`, `Requirement`, `TextUnit`, `ModelElement`. `parentLocalKey` creates `CONTAINS` from a parent element of the same artifact, so the parent must be in the output too.
- `relations`: `DEPENDS_ON`, `PROVIDES`, `REQUIRES`, `CONTAINS` between `{path, localKey}` locators. Targets may be elements in other current artifacts.
- `explicitReferences`: textual references such as `REQ-12` in a comment, with `linkType`. The owner resolves them to trace links. If a plugin returns no reference for a file, the extraction-agent applies its configured `explicitReferences.patterns` to the file content. It uses the line ranges of the returned elements and attaches a match to the innermost element. An element without line numbers covers the whole file. A match above the first element with line numbers, for example a comment above a class, belongs to that element.
- `warnings`: free text, shown to users.

**Code model (optional second skill of an extract plugin).** Input: `projectId` and `currentArtifacts` (`path`, `storagePath`) of the whole current code. Output: `acm`, the code model in the JSON format of ArDoCo, and optional `warnings`. A consumer that runs ArDoCo on the code model uses it instead of building its own. The plugin must stage the files exactly as for `extract`.

**Detect.** Input holds `projectId`, `llmPolicy`, the optional `scopeArtifactIds` (absent means the whole project), `queryServiceUrl` and `params`. Output: `findings` with `type` (`^[a-z0-9-]+$`), `severity`, `title` and `involved` (element keys or trace link ids, each with a `role`). Do not compute fingerprints, ids or lifecycle states; the owner does that.

## Transports

**A2A** (JSON-RPC binding, A2A 1.0). Use it for work that can take minutes, must be cancellable, or reports progress. Serve an Agent Card at `/.well-known/agent-card.json`: copy `contracts/agent-cards/plugin-extract.template.json` (or `plugin-detect.template.json`) and set `name` (the plugin id, recorded as `Run.tool`), `version` (`Run.toolVersion`), `description` and `supportedInterfaces[0].url`. The owner reaches the plugin at its configured host, so only the URL path of the card is used. Keep the skill-invocation extension unchanged (`required: true`). Invocation convention (Stage 2 §1.3):
- the skill id is in `message.metadata["sandman.skill"]`, and the input is the single data part;
- the result is a task artifact named `result` with one data part;
- errors are returned as `{code, message, retryable}` in the status message metadata `sandman.error`.

Callers send `returnImmediately` and poll `GetTask`, and they cancel on timeout. Python plugins can use `sandman_agent.serve(card, skills, settings)` with `SANDMAN_PLUGIN_INBOUND_TOKEN_FILE` set. Skills return `SkillResult(output)` without an event (see `Extract` in the stub).

**Plain HTTP** (`contracts/openapi/plugin-http.yaml`). Use it for fast, deterministic tools or to wrap an existing service:
- `GET /manifest` returns `id`, `version` and `operations`, plus `handles` (extract) or `types` (detect), and `usesLlm`;
- `POST /v1/extract` and `POST /v1/detect` answer synchronously within the caller's timeout (default 10 min). They return `400`/`401` for bad input or a bad token and `503` (problem+json) when the caller should retry;
- `GET /healthz` returns `{"status": "ok"}`.

## Tokens

Every plugin has a token pair, generated by `deploy/gen-secrets.sh` after the plugin name is added to `PLUGINS`. A plugin with more than one calling owner has one inbound token per owner (see Reference plugins):

| Token | Secret | Direction | Rule |
|---|---|---|---|
| inbound | `plugin_in_<id>` (`SANDMAN_PLUGIN_INBOUND_TOKEN_FILE`), or one `plugin_in_<id>_<owner>` per owner (`SANDMAN_PLUGIN_INBOUND_TOKEN_FILES`) | owner → plugin | Check `Authorization: Bearer` with a constant-time compare on every call except `/healthz` and the Agent Card. Owners never send their own service token to plugins. |
| outbound | `svc_token_<id>` (`SANDMAN_SERVICE_TOKEN_FILE`) | plugin → query-service | The caller is `plugin:<id>`. Only `POST /v1/query/cypher` is allowed, read-only and anchored to the project (`{projectId: $projectId}`), and only for projects granted in `config/query/plugins.yaml`. Every other receiver rejects it. |

## Obligations (ADR-0012, D-9)

1. **Never write the KG.** Return results; the owner writes them. Detectors read the KG only through the query-service.
2. **No egress.** Run on the Compose `internal` network only. Prefetch models and parsers at build time.
3. **LLM calls only through the SANDMAN gateway**, using the plugin's own virtual keys `plugin-<id>` and `plugin-<id>-local`. The operator adds them to `deploy/gateway/config.json` and `gateway.env`. Never use provider keys directly.
4. **Honor `llmPolicy`.** For `local-only` projects, use the `-local` key only, which is restricted to local models. If you cannot comply, return an error rather than calling a cloud model.
5. **Read artifacts only from `storagePath`.** It points into the read-only artifact store mount (`/artifacts`). Reject paths outside it.
6. **Send your outbound token only to the query-service URL from your own configuration** (the stub reads `SANDMAN_QUERY_SERVICE_URL`, default `http://query-service:8000`). Treat `queryServiceUrl` in the input as a hint that must match it; on mismatch return 400 and make no outbound call.
7. **Be idempotent.** The same input must give the same output, because owners retry failed calls.

## Reference plugins

| Plugin | Transport | Role |
|---|---|---|
| `tests/plugin-stubs` (compose profile `stubs`) | A2A extract, HTTP detect | Regex stubs for the contract tests and the detect demo. Not for production. |
| `services/code-extractor-ardoco` (default profile) | A2A extract and code-model | Code elements from ArDoCo's code extractors (Java by Eclipse JDT, shell scripts, file typing by Magika). Version `2.1.0+c1`. Called by the extraction-agent (`extract`) and the tlr-agent (`code-model`). |

`code-extractor-ardoco` follows the obligations above and adds these rules for uploaded customer code:
- It runs as user 10001 with a read-only root file system, no capabilities and no egress (network `internal`). It mounts only its two inbound tokens, one per caller: `plugin_in_code-extractor-ardoco` (caller `extraction-agent`) and `plugin_in_code-extractor-ardoco_tlr` (caller `tlr-agent`). `SANDMAN_PLUGIN_INBOUND_TOKEN_FILES=extraction-agent=<file>,tlr-agent=<file>` lists them. The service refuses to start if two callers share a token. Each caller mounts only its own token. The token names the caller, and the skills check it: `sandman.plugin.extract` accepts only `extraction-agent`, `sandman.plugin.code-model` accepts only `tlr-agent`, any other caller gets `FORBIDDEN` (not retryable). It makes no outbound call, so the service token `svc_token_code-extractor-ardoco` is generated but not mounted.
- It reads each file from `storagePath` only if the path is below the artifact store and no component is a symbolic link. It rejects other paths with `INVALID_INPUT` (not retryable).
- It copies the files of a call into a temporary directory at their project `path` and deletes it after the call. It rejects `..`, absolute paths and duplicate paths.
- It rejects a call with more than `SANDMAN_CODE_MAX_FILES` files (default 20 000) or more than `SANDMAN_CODE_MAX_BYTES` bytes (default 64 MB), or a file over `SANDMAN_CODE_MAX_FILE_BYTES` (default 4 MB), with `INVALID_INPUT`. Out of memory fails the task and is not retryable. Only one extraction runs at a time. A call that waits longer than `SANDMAN_CODE_LOCK_TIMEOUT_SECONDS` (default 300) gets a retryable `DEPENDENCY_UNAVAILABLE`. The timeout is the `timeout` of the plugin entry.
- It runs one extraction at a time, because ArDoCo numbers its items with one counter for the whole JVM.

Element mapping of `sandman.plugin.extract` (`label` is `CodeUnit` for kind `code` and `TestCase` for kind `test`):

| ACM item | `kind` | `localKey` | `qualifiedName` |
|---|---|---|---|
| compilation unit | `file` | project path | package and file name |
| class, enum, interface | `type` (`test-class` for tests) | qualified name, nested `Outer.Inner`, `~n` for the n-th duplicate | qualified name |
| method | `method` (`test-method`) | type key `#` name, `~n` for the n-th method of that name in source order | type qualified name `#` name |

A method belongs to the innermost type that lists it (ArDoCo lists the methods of a nested type again in the outer type). `DEPENDS_ON` goes from a file to the files of its imports that are current artifacts. A file that ArDoCo does not recognize as Java or shell code gets only its file element and a warning. ArDoCo 2.1.0 does not resolve `extends` and `implements`, so no type-level `DEPENDS_ON` is returned yet. Raise the `+cN` suffix of the card version when the mapping changes.

## Core detector `model-code`

The detector `model-code` runs in the inconsistency-agent. It needs no plugin. It compares architecture components and code files through the active trace links.

| Parameter | Default | Meaning |
|---|---|---|
| `modelKinds` | `[architecture-model]` | Artifact kinds of model elements. |
| `codeKinds` | `[code]` | Artifact kinds of code files. Kind `test` is not in the default. |
| `modelElementKinds` | `[component]` | Element kinds checked for code. |
| `codeElementKinds` | `[file]` | Element kinds checked for a model element. |
| `excludeGlobs` | `["**/package-info.java", "**/module-info.java"]` | Code paths that never give a finding. |
| `linkApproaches` | `all` | `all` or a list of approach ids whose links count. |
| `maxFindingsPerType` | `500` | Cap per finding type. A warning reports a cut. |

A link to a type or method counts for its file. A rejected link does not count. If the project has no model-code link, the detector reports no finding and one warning. The evaluation is in `docs/tasks/T7.3-model-code-inconsistency-detector.md`.

## Registering

```yaml
# config/extraction/plugins.yaml
plugins:
  - { id: my-extractor, transport: a2a, url: http://my-extractor:8000, skill: sandman.plugin.extract,
      tokenFile: /run/secrets/plugin_in_my-extractor, match: { kinds: [code, test] }, timeout: 10m }
# config/inconsistency/plugins.yaml
plugins:
  - { id: my-detector, transport: http, url: http://my-detector:8001,
      tokenFile: /run/secrets/plugin_in_my-detector, timeout: 10m }
```

## Testing against the stubs

- **Contract tests.** Validate your outputs with `sandman_agent.Contracts(contracts_dir).validate("skills.schema.json#/$defs/PluginExtractOutput", out)`. Non-Python plugins can use any JSON Schema 2020-12 validator on `contracts/`.
- **A2A extract.** `tests/plugin-stubs/tests/test_stubs.py` calls the stub through `sandman_agent.A2aPluginClient`, the client the extraction-agent uses, with `httpx.ASGITransport`. To check your own plugin end to end, point the client at its URL with your inbound token: `await A2aPluginClient(url, token, contracts).extract(input)`.
- **HTTP detect.** The stub test replaces the query-service with an `httpx.MockTransport`. It checks that the plugin sends its own outbound token and a project-anchored query.
- **Running the ArDoCo plugin.** `docker compose -f deploy/docker-compose.yml up -d --wait code-extractor-ardoco`, then call `extract` with `A2aPluginClient` and the token of `deploy/secrets/plugin_in_code-extractor-ardoco`. The benchmark equivalence test downloads code and runs only with `mvn -Pbenchmark verify` in the module. That run also writes the code models of TeaStore and MediaStore to `~/.cache/sandman/benchmark-code/`, which the tests of the tlr-agent read.
- **Running the stub.** `SANDMAN_SERVICE_NAME=plugin-stubs SANDMAN_PLUGIN_INBOUND_TOKEN_FILE=… SANDMAN_SERVICE_TOKEN_FILE=… SANDMAN_ARTIFACT_STORE=… SANDMAN_QUERY_SERVICE_URL=http://query-service:8000 uv run python -m sandman_plugin_stubs` serves extract on port 8000 and detect on port 8001. The walking skeleton (`docker compose --profile stubs up`, fixture `tests/e2e/fixtures/mini/`) exercises both plugins against the real agents.
