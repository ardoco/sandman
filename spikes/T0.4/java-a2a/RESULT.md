# T0.4 check 16 — Java A2A runtime decision (D-6)

Checked on 2026-10-07. Setup: a2a-java `org.a2aproject.sdk` 1.4.0.Final (reference JSON-RPC server + client), Quarkus BOM
**3.33.4** (our LTS; a2a-java itself is built against 3.39.2), Temurin JDK 21.0.12, Maven 3.9.16, macOS arm64. Python side:
`sandman_agent` (a2a-sdk 1.2.2) from the repo venv. ArDoCo `feature/make-inconsistency-great-again` @ `0d1f22b` + NAER @ `d631a70`
(from spike T0.12), LiSSA `ardoco/lissa` `main` @ `960f90d` (1.1.0-SNAPSHOT, built from source with `mvn install -DskipTests`).
All artifacts in the isolated repo `/private/tmp/claude-501/ardoco-spike/m2` (incl. `io.github.ardoco:sandman-agent-lib`, whose
`Contracts` validator the spike agent reuses).

## Result

| Criterion | Result | Evidence |
|---|---|---|
| (a) Custom `AgentExecutor` with the SANDMAN convention in a plain Quarkus app (a2a-java reference JSON-RPC server) | **PASS on Quarkus 3.33.4** (3.39.x not needed) | Card at `/.well-known/agent-card.json` (served from `contracts/agent-cards/tlr-agent.json`), JSON-RPC at `/a2a` (bearer filter + reroute, see W-1). Java client run of all `skillInvocation` vectors: `valid input → COMPLETED` (artifact `result`), `missing skill metadata → REJECTED INVALID_INPUT`, `schema-invalid input → REJECTED INVALID_INPUT`, `unknown skill → REJECTED UNKNOWN_SKILL`; `SendMessage` with `returnImmediately` returns the task (`SUBMITTED`, or `REJECTED` directly); `GetTask` unknown id → `TaskNotFoundError` (-32001); `CancelTask` on a running task → `CANCELED` (flag seen by the skill loop). Missing/invalid token → 401 on `/a2a` and `/`. Three settings needed: W-1, W-2, W-3. |
| (b) tlr-agent-like skeleton with a2a-java + ArDoCo (pinned) + LiSSA builds and starts without dependency conflicts | **PASS with caveats** | `mvn -Pardoco package` → `BUILD SUCCESS`, starts in 0.4 s. In the running Quarkus app the skill with `approaches:[ardoco-id]` ran `InconsistencyDetection` on MediaStore: `ARDOCO findings=42 byType={ModelEntityAbsentFromTextInconsistency=6, TextEntityAbsentFromModelInconsistency=36}` — identical to spike T0.12 (42 = 6 + 36), so protobuf 3→4 (4.33.2) and JNA 5.18.1→5.8.0 do not break the CoreNLP/local path (RK-22). LiSSA's `ModelUMLPreprocessor` on `ms.uml` in the same JVM: 24 elements (1 artifact + 23 endpoints, same as T0.12). `dependencyConvergence`: 0 errors without ArDoCo/LiSSA, 13 with them, **none involving a2a-java or Quarkus** (all between ArDoCo and LiSSA, see "Dependency conflicts"). Caveats: LangChain4j is downgraded to LiSSA's 1.16.3, two SLF4J providers, `quarkus-app/lib` grows from 34 MB to 673 MB, RSS 1.7 GB after two ArDoCo runs. |
| (c) Cross-language interop (check 9) | **PASS with one required change (W-2)** | Java client → Python agent (`py/py_agent.py`, `sandman_agent.a2a.serve`, tlr-agent card, dummy `recover-trace-links`): all 4 vectors, valid call, `GetTask` unknown, `CancelTask` as expected. Python `A2aClient` → Java agent: fails on every call with `ExtensionSupportRequiredError: Required extension 'https://sandman.invalid/a2a/extensions/skill-invocation/v1' was not requested by the client` unless the client sends `A2A-Extensions: <uri>`; with that header (`SPIKE_EXT_HEADER=1`) all cases pass and `linkCount` arrives as `int`. |
| Fallback (hand-written JSON-RPC endpoint) | NOT VERIFIED (not needed) | Size sketch only: `SendMessage`/`GetTask`/`CancelTask` + static card on `com.sun.net.httpserver.HttpServer` with Jackson ≈ 250–350 lines (JSON-RPC envelope and errors, in-memory task store with state machine, proto-JSON shapes for `Task`/`Message`/`Part`/`Artifact`, executor thread pool). It would also have to replicate everything the wire notes below list, by hand. |

**Recommendation for D-6: keep `a2a-java` on Quarkus 3.33.4** (default holds; both criteria pass). Record in the ADR-0002 amendment
and in `agent-lib-a2a`:
1. `a2a.authorization.required=false` (or a real `TaskAuthorizationProvider`), plus our own bearer filter on the Vert.x router (W-3).
2. Serve JSON-RPC at the card's path: a2a-java only routes `POST /` (W-1). Either reroute `/a2a` → `/` (done here, 1 route) or change
   the cards' interface URL to the root path. Guard `/` as well.
3. Required extension: add `A2A-Extensions: https://sandman.invalid/a2a/extensions/skill-invocation/v1` to every A2A client request
   (Python `A2aClient`: one default header on its `httpx.AsyncClient`; Java: `ClientCallContext` headers), **or** set
   `"required": false` in the cards (W-2). Proposal: send the header (spec-conform), keep `required: true`.
4. Normalize integral doubles to integers on both sides in Java too (`jsonValue`, as in Python) — W-4.
5. ArDoCo + LiSSA in one agent: pin LangChain4j explicitly (ArDoCo wants 1.18.1, LiSSA's direct dependency 1.16.3 wins by
   nearest-wins), exclude LiSSA's `slf4j-simple`, and align `model-provider` (LiSSA is built against 2.0.1, 2.1.0-SNAPSHOT wins
   and works for the UML preprocessor). R-030's separate adapter modules make these exclusions local.

## Wire-format notes (from `wiretap.py` captures)

* **W-1 path**: a2a-java `A2AServerRoutes` registers `router.post("/")` only (plus card routes); `/a2a` → 404 without our reroute.
  Python serves the card's interface path.
* **W-2 required extension**: a2a-java validates on *every* method (`JSONRPCHandler.validateVersionAndExtensions`, spec §3.6.2)
  that each `required: true` card extension is listed in the `A2A-Extensions` request header. The Python SDK server does not check
  this, and the Python client does not send the header.
* **Version header**: a2a-java treats a missing `A2A-Version` header as `0.3` and rejects with -32009 `VERSION_NOT_SUPPORTED`
  (seen with plain curl). Both SDK clients send `A2A-Version: 1.0`; hand-written clients (curl tests, fallback) must too.
* **W-3 authorization**: a2a-java 1.4 is fail-closed: without a `TaskAuthorizationProvider` bean every call, including
  `SendMessage`, fails with -32001 **"Task not found"** (`a2a.authorization.required=true` default) — misleading error.
* **W-4 numbers**: both servers emit data-part numbers as protobuf `Struct` doubles: `"linkCount":1.0` on the wire from Java *and*
  Python agents; the Java client gets `Double`, the Python client gets `int` only because of `json_value`. The Java server receives
  `Double` for every input number. Java must apply the same normalization before schema validation, equality checks and JCS
  hashing (done in `Agent.jsonValue`; whether networknt accepts `1.0` as `integer` was not tested).
* **Request shape**: the Java client prints proto defaults: `"tenant":""`, `"contextId":""`, `"taskId":""`, per part
  `"metadata":{}, "filename":"", "mediaType":""`, `"extensions":[]`, `"referenceTaskIds":[]`, `"configuration":{"acceptedOutputModes":[],
  "returnImmediately":true}`, `"metadata":{}`. Python sends only set fields. Both servers accept both (a2a-java normalizes blank ids).
  Field names agree (`returnImmediately`, `messageId`, `ROLE_USER`, `parts[].data`).
* **Response shape**: a2a-java includes defaults (`"artifacts":[]`, `"history":[]`, `"description":""`, `"extensions":[]`) and a
  status `timestamp` already on `SUBMITTED`; Python omits empty fields and has no timestamp on the first `SUBMITTED`. State names are
  identical (`TASK_STATE_*`). `sandman.error` metadata (`code`, `message`, `retryable`) round-trips unchanged in both directions.
* **returnImmediately semantics differ**: Java returns `REJECTED` directly in the `SendMessage` result when the executor rejects
  before the response is built; Python returns `SUBMITTED` and the rejection appears on the next `GetTask`. Both satisfy the vectors
  (`SUBMITTED_OR_LATER` + polling); callers must not assume the first state.
* **GetTask unknown id**: identical JSON-RPC error on both: `{"code":-32001,"message":"Task not found","data":[{"@type":
  "type.googleapis.com/google.rpc.ErrorInfo","reason":"TASK_NOT_FOUND","domain":"a2a-protocol.org","metadata":{}}]}`; Java client →
  `A2AClientException` caused by `TaskNotFoundError`, Python → `TaskNotFoundError`.
* **Card**: a2a-java re-serializes the card with Gson (`'` → `'`, `=` → `=`, different key order); `securitySchemes.
  bearer.httpAuthSecurityScheme`, `securityRequirements[].schemes.bearer.list` and extension `params` survive; both clients parse
  both cards. Security schemes are not enforced by either SDK.
* Validation messages from networknt are localized (German on this machine: "stimmt nicht mit dem Regex-Muster … überein"); set
  `Locale.ROOT`/English in `Contracts` for stable `sandman.error.message`.

## Dependency conflicts (`-Pardoco`, `mvn enforcer:enforce@convergence`, `dependency:tree -Dverbose`)

Convergence errors (13, all ArDoCo ↔ LiSSA or inside ArDoCo; 0 without `-Pardoco`):
LangChain4j `langchain4j`, `-core`, `-open-ai`, `-ollama` 1.18.1 (ArDoCo) vs 1.16.3 (LiSSA, **wins**); `io.github.ardoco.tlr:
model-provider` 2.1.0-SNAPSHOT (wins) vs 2.0.1 (LiSSA); `json-schema-validator` 3.0.8 (sandman-agent-lib, wins) vs 2.0.0 (ArDoCo);
`tree-sitter` 0.26.6/0.26.3, `jedis` 7.5.2/7.5.0 (LiSSA internal); Eclipse `osgi`/`core.runtime`/`equinox.common`, `jaxb-api`,
`joda-time` (ArDoCo internal).

Quarkus-BOM-managed overrides that change versions of ArDoCo/LiSSA dependencies (not reported by convergence):
`protobuf-java` 3.25.5 → **4.33.2**, `jna` 5.18.1 → **5.8.0**, Jackson 2.22.2 → 2.21.7, `jakarta.servlet-api` 4.0.4 → 6.0.0,
`gson` 2.14.0 → 2.13.2, `httpclient5` → 5.5.2, `slf4j-api` 2.0.18/2.0.19 → 2.0.17, `commons-lang3` → 3.20.0, also
`protobuf-java-util` 4.35.0 → 4.33.2 for a2a-java itself. The MediaStore run (42 = 42) shows no effect on the non-LLM path.
LiSSA puts `slf4j-simple` on the compile classpath: `SLF4J(W): Class path contains multiple SLF4J providers` (JBoss provider wins).
Build warning in both variants: `Failed to index io.grpc.stub.Abstract*Stub` (a2a `spec-grpc` without grpc-stub; harmless).

## Files and how to run

`pom.xml` (standalone, groupId `io.github.ardoco`, artifactId `sandman-spike-t0-4-java-a2a`; profile `ardoco` adds `pipeline-id` +
`lissa` and `src/ardoco/java`), `src/main/java/.../spike/Agent.java` (executor, card producer, bearer filter + reroute),
`ClientCli.java` (Java client run), `src/ardoco/java/.../ArdocoProbe.java`, `py/py_agent.py`, `py/py_client.py`, `wiretap.py`
(logging proxy for wire JSON).

```sh
M2=/private/tmp/claude-501/ardoco-spike/m2          # ArDoCo via spikes/T0.12/bootstrap.sh; LiSSA: git clone ardoco/lissa @960f90d, mvn install -DskipTests
(cd ../../.. && mvn -B -q -Dmaven.repo.local=$M2 -pl libs/java/agent-lib -am install -DskipTests)
mvn -B -Dmaven.repo.local=$M2 [-Pardoco] package dependency:build-classpath -Dmdep.outputFile=target/cp.txt
java -jar target/quarkus-app/quarkus-run.jar &      # :8090, token SPIKE_TOKEN (default spike-token)
python3 wiretap.py 8091 http://localhost:8090 &     # optional wire log
java -cp target/classes:$(cat target/cp.txt) edu.kit.kastel.mcse.ardoco.sandman.spike.ClientCli http://localhost:8091 [ardoco-id]
# from the repo root:
uv run python spikes/T0.4/java-a2a/py/py_agent.py 8092 &
SPIKE_EXT_HEADER=1 uv run python spikes/T0.4/java-a2a/py/py_client.py http://localhost:8091   # Python -> Java
java -cp ... ClientCli http://localhost:8092                                                   # Java -> Python
```

Not done: Quarkus 3.39.x run (not needed), JDK 26 runtime, `@QuarkusTest` form, completion events/NATS, fallback implementation.
