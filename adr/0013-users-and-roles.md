---
status: accepted
date: 2026-10-06
---

# Users and roles

## Context and Problem Statement

SANDMAN is used by multiple users with roles (Q-8), on several projects, for continuous use. Who authenticates users, where are roles stored, and how do owner services (e.g., the inconsistency-agent's triage API) learn who acts?

## Decision Drivers

* Per-project permissions (read, triage, ingest/trigger) and global administration.
* Open source only, no Enterprise features (Q-10); Memgraph Community has no RBAC.
* Few additional containers on a single server.
* CI ingestion needs non-interactive credentials.
* Path to an institutional identity provider later.

## Considered Options

* A: Built-in user store in the query-service (SQLite, Argon2id hashes, personal API tokens), roles checked there; owners trust the query-service as forwarder of the actor.
* B: External OIDC identity provider (e.g., Keycloak, Apache-2.0) with roles as claims; services validate tokens.
* C: Reverse proxy authentication (e.g., oauth2-proxy in front of the console) passing user headers.

## Decision Outcome

Chosen option: "A". Roles: global `admin`, global `analyst` (raw Cypher across projects), per-project `viewer` < `triager` < `maintainer`. The query-service is the only user entry point and the policy decision point. It forwards `X-Sandman-Actor` and `X-Sandman-Roles` to owner APIs; owners accept these headers only when the caller's service token belongs to the query-service. First admin from a bootstrap secret.

Q-12 answered 2026-10-06: built-in user store.

### Consequences

* Good, because no extra container; simple to test; works offline.
* Good, because authorization lives in one place.
* Bad, because no SSO; password management is ours (ceiling: add OIDC login to the query-service — the actor/roles forwarding stays unchanged).
* Bad, because per-project isolation of raw Cypher is impossible in one Memgraph Community database; raw Cypher is restricted to global roles, NL queries are project-scoped best effort.

### Confirmation

Authorization tests per endpoint and role; test that owners reject actor headers from non-forwarder callers.

## Pros and Cons of the Options

### A: Built-in store

* Good, because minimal.
* Bad, because no SSO.

### B: OIDC provider

* Good, because SSO, standard tokens, institutional login possible.
* Bad, because Keycloak adds a JVM service plus database; every service validates tokens.

### C: Proxy authentication

* Good, because no auth code in services.
* Bad, because roles still need a store; still needs an identity provider behind the proxy.

## More Information

* Related: ADR-0009, ADR-0011.

## Amendments

* 2026-10-06, review decision D-2 (option A, R-036): projects are isolated; all reads are per project. The global `analyst` role is removed. Raw Cypher and NL queries require project membership (`viewer`+) and pass the scope guard (ADR-0009 amendment); unscoped queries only for `admin`. Service callers may read any project through scoped queries; plugin callers (`plugin:<id>`) only the projects granted in `config/query/plugins.yaml`, and are rejected by every other receiver. Projects with confidential artifacts will run in separate deployments (future).
* 2026-10-06, review decision D-4 (R-033, R-045): each service holds only its own token (`svc_token_<name>`); receivers verify against a hash file (`svc_tokens_verify`). Trusted forwarders: the query-service for `X-Sandman-Actor`/`X-Sandman-Roles`, the orchestrator for `sandman.actor` in A2A metadata. Console sessions: server-side session table (revocable), cookies `HttpOnly` + `SameSite=Strict` (+ `Secure` behind TLS), CSRF tokens on all forms, `/v1/*` accepts bearer tokens only (no cookies), login rate limiting, forced change of the bootstrap password at first login.
