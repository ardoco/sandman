# A2A with `a2a-sdk` 1.2.2 (Python) — findings from T0.2

Checked against the running SDK in `libs/python/sandman-agent/tests/test_a2a.py`.

| Item | Result | Consequence |
|---|---|---|
| Agent Cards in `contracts/agent-cards/` (V-19) | All cards incl. plugin templates parse into the SDK's `AgentCard` (proto JSON names `supportedInterfaces`, `protocolBinding`, `securitySchemes.bearer.httpAuthSecurityScheme`, `securityRequirements`) | No contract change |
| `SendMessage` configuration (V-5, V-19) | Field is `configuration.returnImmediately` | As assumed in Stage 2 §1.3 |
| Unknown task in `GetTask` (V-5) | Client raises `a2a.utils.errors.TaskNotFoundError` ("Task not found") | Re-exported as `sandman_agent.TaskNotFoundError`; the orchestrator treats it as retryable |
| Data parts | Carried as protobuf `Struct`/`Value`: every number arrives as float (`1` → `1.0`) | The library converts integral floats back to `int` before validation (`json_value`); Java must do the same |
| Task creation | With `returnImmediately`, the executor must enqueue the `Task` (state `SUBMITTED`) before status updates | Done in the library's executor |
| Authentication | The SDK does not check bearer tokens; the request headers reach the executor via `call_context.state["headers"]` | Library middleware rejects calls without an accepted token on the JSON-RPC path (401); the Agent Card stays public |
| Quarkus version | `a2a-java` 1.4.0.Final builds against Quarkus 3.39.2, our BOM pins Quarkus LTS 3.33.4 | To be settled in T0.4 check 16 |

Local note: Testcontainers for Python does not read the Docker context. With Docker Desktop on macOS set
`DOCKER_HOST=unix://$HOME/.docker/run/docker.sock` to run `libs/python/sandman-agent/tests/test_integration.py`
(the tests are skipped when no daemon is reachable).
