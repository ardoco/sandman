# TX.4 Tree-sitter code extraction for Go, Rust and TypeScript (design note)

Status: proposal (2026-11-07) · Effort estimate: M (one language set, one plugin) · Depends on: T2.6 (done), TX.1 (done) · No implementation in this note

## Purpose

ADR-0017 admits agents in TypeScript, Go and Rust. The plugin `code-extractor-ardoco` extracts only Java and shell. This note assesses tree-sitter as a way to extract code elements for Go, Rust and TypeScript.

Sources: `adr/0017-additional-agent-languages.md`, `docs/tasks/T2.2-adapted-lissa.md`, `docs/tasks/T2.6-ardoco-code-extraction.md`, `contracts/agent-cards/code-extractor-ardoco.json`, `contracts/skills.schema.json`, `libs/typescript/sandman-agent`, and web checks on 2026-11-07 (links at the end).

## 1. Scope facts from the repository

- ADR-0017 scope: agent kits and plugins in TS, Go, Rust. It says nothing about code extraction for these languages. It states that plugins can use any language.
- T2.2 lists the LiSSA "tree-sitter code" preprocessor as dropped. Reason: it is not in the published r2c setup and it uses LangChain4j. This decision does not apply to a new plugin. It covers only the LiSSA port.
- ArDoCo 2.1.0 `CodeConfiguration` does not use its ANTLR extractors (Python, C++). It has no Go, Rust or TS extractor. Equivalence with ArDoCo's own code model (T2.6 criterion) is therefore impossible for these languages.
- Consequence: elements for Go, Rust and TS are "tree-sitter model" elements. They are not "ArDoCo code model" elements. T2.1b (SAD to code) reads the ACM through `sandman.plugin.code-model`. It would not see these languages. See open question 2.

## 2. Existing output contract (what must be emitted)

Plugin `code-extractor-ardoco` 2.1.0+c1 speaks A2A with skill `sandman.plugin.extract`. Config: `config/extraction/plugins.yaml`, `match: {kinds: [code, test]}`.

Input `PluginExtractInput`: `projectId`, `llmPolicy`, `artifacts[]` (`artifactVersionId`, `path`, `kind`, `storagePath`, `contentHash`), `context.currentArtifacts[]` (`path`, `storagePath`). Files are read from the read-only `/artifacts` mount.

Output `PluginExtractOutput`: `elements`, `relations`, `explicitReferences`, `warnings`.

| Field | Rule |
|---|---|
| `elements[].path` | Project path of the artifact |
| `elements[].localKey` | Unique in the artifact. Stable across versions. Never a counter id |
| `elements[].label` | `CodeUnit` for kind `code`. `TestCase` for kind `test` |
| `elements[].kind` | `file`, `type`, `method` (tests: `test-class`, `test-method`) |
| `elements[].name`, `qualifiedName` | Simple name. Qualified name |
| `elements[].startLine`, `endLine` | 1-based. Omit `endLine` if unknown |
| `elements[].parentLocalKey` | Makes a `CONTAINS` edge in the same artifact |
| `elements[].properties` | Not stored by the extraction-agent today (T2.6 open question 4) |
| `relations[]` | Type `DEPENDS_ON`. Only between known current artifacts. No relation to unknown targets |

T2.6 mapping rules to keep:
- One `file` element per file. Types have the file or the enclosing type as parent. Methods have the type as parent.
- Packages and directories are not elements. The package goes into `qualifiedName`.
- Overloads get `~n` in source order (n at least 2).
- The extraction-agent applies `explicitReferences.patterns` to plugin output. The plugin returns no references.
- Limits, staging and path validation from T2.6 apply (caps, no `..`, no symlink escape, delete temp data).
- No egress, no LLM, no KG access. Inbound bearer token `SANDMAN_PLUGIN_INBOUND_TOKEN_FILE`. `/healthz`.

## 3. Proposed plugin shape

One new plugin `code-extractor-treesitter`. It is a separate service. It does not change `code-extractor-ardoco`.

| Decision | Choice | Reason |
|---|---|---|
| Language | Python 3 | Lowest effort. Wheels exist for the grammars. The plugin helper `sandman_agent` and the stub app are the reference |
| Kit | `libs/python/sandman-agent` (plugin mode in `a2a.py`) | Exists and is tested. Reuse the stub plugin pattern in `tests/plugin-stubs` |
| Binding | `tree-sitter` (py-tree-sitter) with one wheel per grammar: `tree-sitter-go`, `tree-sitter-rust`, `tree-sitter-typescript` | Pins per grammar. Small image. No on-demand download |
| Not chosen: `tree-sitter-language-pack` | Rejected for now | It downloads parsers on demand (per search result). This violates the no-egress and no-run-time-download rules. Re-check if a bundled mode exists |
| Skill | `sandman.plugin.extract` only | `code-model` skill is ACM-specific. See question 2 |
| Routing | A second `plugins.yaml` entry with `match` on `kinds: [code, test]` plus a suffix or `format` filter | Verify that the plugin router can match by suffix or format. If not, the router needs a small change |
| Deploy | Compose service on `internal`, secrets `plugin_in_code-extractor-treesitter` and `svc_token_...`, add to `PLUGINS` in `gen-secrets.sh`, `site/src/components.yaml`, `THIRD-PARTY.md`, `docs/plugins.md` | Same as T2.6 item 9 |

Alternatives:
- Node.js plugin on the TS kit with `web-tree-sitter`. Works and tests the TX.1 kit with a real plugin. Costs: grammar `.wasm` files must be bundled and version-matched. The kit has no plugin-specific tests today (only a config setting for the token was found).
- Java plugin with `jtreesitter` inside `code-extractor-ardoco`. Not recommended. `jtreesitter` uses the Java FFM API and needs a recent JDK (to confirm). Native libraries and grammar `.so` files must be loaded. The project uses Java 21.
- Rust plugin. Not recommended. Rust kit does not exist (ADR-0017 tier 2).

Parsing is per file. Cross-file relations (imports) need only the path list from `context.currentArtifacts`, no semantic analysis.

## 4. Grammars, bindings and licences

Status "to verify" means the search results did not confirm it. Check before pinning.

| Component | Role | Licence | State (2026-11-07) |
|---|---|---|---|
| tree-sitter core | Parser runtime | MIT | Active. 0.25/0.26 line |
| py-tree-sitter (`tree-sitter`) | Python binding | MIT | Active. To verify exact version |
| `tree-sitter-go` | Go grammar | MIT | v0.25.0 latest release on GitHub |
| `tree-sitter-rust` | Rust grammar | MIT | To verify version |
| `tree-sitter-typescript` | TS and TSX grammar | MIT | crates.io 0.23.2, about 2 years old. Low release cadence. To verify PyPI version |
| `tree-sitter-language-pack` | Bundle of 300+ grammars | MIT (grammars must be permissive) | Active, repo moved to `kreuzberg-dev`. Downloads parsers on demand |
| `web-tree-sitter` | npm WASM binding | MIT | Active (0.26.x/0.27.x). Free of dependencies. Needs `tree.delete()` |
| `tree-sitter` (node-tree-sitter) | npm native binding | MIT | Weak. 0.25.1 after 13 months. Node 24 build problems. `tree-sitter-typescript` peer `^0.21` |
| `jtreesitter` (`io.github.tree-sitter`) | Official Java binding | MIT | 0.26.0 on Central (Dec 2025). GitHub shows 0.26.1. FFM, JDK 22+ expected (to verify) |
| `io.github.bonede:tree-sitter` | Community Java binding | MIT | 0.26.6 on Central |
| `ch.usi.si.seart:java-tree-sitter` | Community Java JNI | MIT | 1.12.0 (Feb 2024). Stale |

All components are permissive. They fit NFR-13 (open source). Add them to `THIRD-PARTY.md`.

Grammar ABI must match the runtime ABI. Pin runtime and grammars together. Use a lockfile (`uv.lock`).

## 5. Element mapping

Queries: write own `.scm` files in the plugin. Do not depend on a `tags.scm` file in the grammar repositories (not confirmed to exist for each grammar). Query names below are tree-sitter node types.

General rules:
- `file` element: `localKey` = project path. `qualifiedName` = module or package + file name.
- Test files go to kind `test` by the extraction-agent's routing. The plugin emits `test-class` and `test-method` for them.
- Syntax errors: tree-sitter returns a tree with `ERROR` nodes. Emit elements for valid nodes. Add a warning with the file path.
- Methods: `localKey` = type key + `#` + name, plus `~n` for repeated names (same as T2.6).

### Go

| Go node type | Element | Notes |
|---|---|---|
| `package_clause` | none | Package name goes into `qualifiedName` (`pkg.Type`) |
| `type_spec` with `struct_type` | `type`, `properties.typeKind` = `struct` | Parent: file |
| `type_spec` with `interface_type` | `type`, `typeKind` = `interface` | Method specs inside are not elements (YAGNI) |
| other `type_spec` | `type`, `typeKind` = `alias` | Optional |
| `function_declaration` | `method` (parent: file) | `localKey` = `func#Name` |
| `method_declaration` | `method` (parent: receiver type in the same file, else file) | Receiver type from `receiver` field. Strip `*` |
| `import_spec` | `DEPENDS_ON` file to files in the same directory of the import path suffix | Resolve with `go.mod` module path. Skip standard library and unknown targets |
| `_test.go`, `func TestXxx` | `test-method` | Test files: `test-class` not used |

### Rust

| Rust node type | Element | Notes |
|---|---|---|
| `mod_item` (inline `mod x { }`) | `type`, `typeKind` = `module` | Parent: file or enclosing module. File modules: path gives the name |
| `struct_item`, `enum_item`, `union_item` | `type`, `typeKind` = `struct`, `enum`, `union` | |
| `trait_item` | `type`, `typeKind` = `trait` | |
| `impl_item` | none | Methods attach to the type named in the `type` field. Trait impls: `DEPENDS_ON` type to trait if both are in current artifacts |
| `function_item` (top level) | `method` (parent: file or module) | |
| `function_item` inside `impl_item` or `trait_item` | `method` (parent: type) | |
| `use_declaration` | `DEPENDS_ON` file to the file of `crate::` or `self::` or `super::` path | Resolve by path rules. Skip external crates |
| `#[test]` attribute on `function_item` | `test-method` | Attribute is a sibling node (`attribute_item`) before the function. `#[cfg(test)] mod` gives `test-class` |

Macros (`macro_rules!`, procedural macros) are not expanded. Items generated by macros are missed. State this as a known gap.

### TypeScript (and TSX)

| TS node type | Element | Notes |
|---|---|---|
| `class_declaration`, `abstract_class_declaration` | `type`, `typeKind` = `class` | Also `class` expressions assigned to a name (optional) |
| `interface_declaration` | `type`, `typeKind` = `interface` | |
| `enum_declaration` | `type`, `typeKind` = `enum` | |
| `type_alias_declaration` | `type`, `typeKind` = `alias` | Optional |
| `internal_module`, `module` (`namespace X`) | `type`, `typeKind` = `namespace` | Parent chain gives nesting |
| `method_definition`, `method_signature` | `method` (parent: type) | Constructors included, name `constructor` |
| `function_declaration` | `method` (parent: file) | |
| `lexical_declaration` with an `arrow_function` or `function_expression` value | `method` (parent: file) | Common style. Include |
| `import_statement` (`from "./x"`) | `DEPENDS_ON` file to resolved relative file | Try suffixes `.ts`, `.tsx`, `/index.ts`. Skip bare specifiers (packages). Ignore `tsconfig` paths in v1 |
| test calls `describe`, `it`, `test` (vitest, jest) | `test-class` and `test-method` | Name = first string argument. Key = path of describe names |

Use grammar `typescript` for `.ts` and `tsx` for `.tsx`. JavaScript is out of scope for v1 (the `tree-sitter-javascript` grammar is a separate package).

### Mismatches with the ArDoCo code model

- ArDoCo has packages. Here, packages and directories are not elements (T2.6 decision stays).
- ArDoCo `ControlElement` has no parameter types. Tree-sitter can give them. Do not use them in keys in v1, to keep the T2.6 rule.
- Go and Rust functions are free-standing. Mapping them to `method` is a choice. Keep it. The KG has no `function` kind. Use `properties.callable` = `function` or `method` for later.
- Line ranges: tree-sitter ranges are 0-based. Add 1. The node includes leading attributes or doc comments only if they are part of the node. Rust doc comments and Go comments are siblings. Document this.

## 6. Risks

| Id | Risk | Mitigation |
|---|---|---|
| 1 | Elements are not ArDoCo code model elements. T2.1b cannot link SAD to Go, Rust, TS code | Keep the two plugins separate. Decide question 2 before use |
| 2 | `TypeScript` grammar release cadence is low | Pin. Check TS syntax coverage with fixtures (decorators, `satisfies`, `using`) |
| 3 | Grammar and runtime ABI drift | Pin together. Add a smoke test that parses one fixture per language |
| 4 | Macros, build tags, generated code are invisible | Document as gap. Skip `vendor/`, `node_modules/`, `target/`, generated files by path rule |
| 5 | Import resolution is heuristic | Resolve only inside current artifacts. Never create relations to unknown targets |
| 6 | Untrusted code is parsed by a native library | Run as a non-root user, no egress, read-only mount, per-call timeout, file-size cap (T2.6 limits). Tree-sitter is error-tolerant, but report crashes as `INTERNAL` |
| 7 | Memory and time on large repositories | Parse file by file and free trees. Cap files and bytes as in T2.6 |
| 8 | Routing: one `code` kind for all languages. Two plugins may both match | Verify router semantics. Match by suffix or `format` so each file goes to one plugin |
| 9 | `properties` are not stored (T2.6 open question 4) | `typeKind` is lost until that change. Encode the kind in `kind` only if needed |
| 10 | Stable `localKey` for anonymous items (arrow functions, closures) | Name only named items. Skip anonymous ones |

## 7. Open questions

1. Router: can `plugins.yaml` match by suffix or `format`? Default: add it if missing (small change).
2. Should T2.1b see Go, Rust and TS? Default: no. Add a `code-model` skill to the new plugin only if a SAD-to-code approach needs it.
3. Plugin runtime: Python (default) or Node.js? Default: Python. Choose Node.js if the team wants a first real TX.1 consumer.
4. `tree-sitter-language-pack`: does it have an offline bundle mode? Default: use per-grammar wheels.
5. Are Go and Rust agents planned? ADR-0017 Q-21 says no current agent exists. Default: build only when the first ingest of such a repository is planned.
6. Is JavaScript needed? Default: no.
7. Should a method key use the parameter types? Default: no (same as T2.6).
8. Store `properties` on elements (T2.6 open question 4)? Default: yes, as a separate change. It makes `typeKind` useful.

## 8. Effort estimate

| Item | Effort |
|---|---|
| Python plugin skeleton, staging, limits, Agent Card, compose, secrets | S (reuse T2.6 patterns and the stub plugin) |
| TypeScript queries, mapping, import resolution, tests | S |
| Go queries, mapping, `go.mod` resolution, tests | S |
| Rust queries, mapping, `mod` and `use` resolution, tests | M |
| Router change (if needed), docs, site, `THIRD-PARTY.md` | S |
| Fixtures and two-run determinism test | S |
| Total | M (about 1 to 1.5 times T2.6 without the Magika and memory work) |

Acceptance sketch: per language, a fixture project gives the expected element set (kind, path, qualified name, lines). Two runs give equal output. Syntax errors give warnings, not failures. Path validation and caps return `INVALID_INPUT`. Output validates against `PluginExtractOutput`.

## 9. Recommendation

- Build: not now. No Go, Rust or TS agent or ingest target is planned (ADR-0017 Q-21). T2.6 covers the current benchmark projects (Java).
- If the need appears, build one Python plugin `code-extractor-treesitter` with per-grammar wheels.
- Order: TypeScript first (the repository itself has TS code, and the TX.1 kit gives a real test subject), then Go, then Rust (tier 2 in ADR-0017).
- Do not use Java or Node bindings for this plugin in v1. `web-tree-sitter` is the right choice only if the plugin moves to Node.js. Avoid `node-tree-sitter` (stale).
- Before building, decide questions 1 and 2.

## Sources

- jtreesitter: https://central.sonatype.com/artifact/io.github.tree-sitter/jtreesitter and https://github.com/tree-sitter/java-tree-sitter/releases
- tree-sitter-language-pack: https://github.com/Goldziher/tree-sitter-language-pack/
- Node bindings: https://snyk.io/advisor/npm-package/web-tree-sitter and https://npmtrends.com/tree-sitter-vs-web-tree-sitter
- Grammars: https://github.com/tree-sitter/tree-sitter-go, https://github.com/tree-sitter/tree-sitter-rust, https://github.com/tree-sitter/tree-sitter-typescript
