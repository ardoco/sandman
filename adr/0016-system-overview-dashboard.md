---
status: accepted
date: 2026-10-06
---

# System overview dashboard

## Context and Problem Statement

Operators need one place that shows whether SANDMAN works: which services are healthy, which are working or idle, what the knowledge graph contains, and how the LLM gateway is used (FR-21–FR-23, added 2026-10-06). The inconsistency console (ADR-0011) is a project-scoped user tool inside the query-service. Where does the overview live, who may use it, and where does it get its data?

## Decision Drivers

* The overview must keep working when the component it reports on is down (e.g. the query-service or an agent).
* System-wide information (all services, all projects, gateway costs) is operator information.
* Few moving parts: no new language or build chain; read-only access wherever possible.
* No Enterprise or proprietary tools (Q-10); Memgraph Lab stays excluded.

## Considered Options

* Placement: (P1) separate service `dashboard`; (P2) extra pages in the query-service console.
* Access: (A1) global admins only, own login, port on 127.0.0.1; (A2) all users, project-scoped via the query-service.
* Graph rendering: (G1) vendored Cytoscape.js (MIT) in server-rendered pages; (G2) a separate SPA; (G3) Memgraph Lab (proprietary, excluded).
* Resource usage: (R1) cAdvisor in an optional profile, read by the dashboard; (R2) Docker socket mounted into the dashboard; (R3) none.

## Decision Outcome

Chosen: **P1 + A1 + G1 + R1** (P1 and A1 decided by the user on 2026-10-06, together with all proposed extras).

* `dashboard`: Python service (FastAPI + Jinja2 + vendored htmx, Cytoscape.js and uPlot; no build chain), port `127.0.0.1:8090`, admin login with its own secret (`dashboard_admin_password`), server-side sessions with the same rules as the console (ADR-0013 amendment). It depends only on Memgraph and NATS at startup, not on agents or the query-service.
* **Data sources** (all reads; the only writes are dead-event replay and workflow cancel through the orchestrator API, and its own SQLite samples):
  * every SANDMAN service exposes `GET /v1/status` (`ServiceStatus`: `working`/`idle`, active tasks, last activity, service-specific details); together with `/readyz` this gives health and activity;
  * Memgraph via read transactions (graph view, project statistics, `SHOW STORAGE INFO`), with timeout and memory limit;
  * NATS monitoring endpoint (`/jsz`) and an ephemeral ordered consumer for the event viewer;
  * orchestrator REST API for the workflow monitor and dead events;
  * Bifrost `/metrics` (Prometheus) and management API for LLM statistics and budgets, authenticated with the gateway admin credentials;
  * cAdvisor (optional profile `resources`) for container memory/CPU;
  * own SQLite (`dashboard-data`) for history samples (health, activity, LLM usage; 30 s interval, 14 days retention).
* **Admins only:** an operator tool for global admins; it may show all projects, consistent with admins' unscoped reads (ADR-0013 amendment).

### Consequences

* Good, because the overview survives failures of the components it monitors.
* Good, because no write path into the KG exists (read transactions) and no new language or build chain is introduced.
* Bad, because the dashboard is privileged: it holds the Memgraph password, the gateway admin password and a service token accepted by the orchestrator; cAdvisor needs read-only host mounts. Mitigations: localhost-only port, own admin login, read transactions, cAdvisor only in an opt-in profile on the internal network.
* Bad, because a second web UI next to the console must be maintained (shared templates and styles where possible).

### Confirmation

Dashboard tasks T1.9–T1.11: every service reports `/v1/status` (contract tests); the overview shows a killed agent as down within one polling interval; graph view and Cypher box run in read transactions (a write attempt fails); gateway statistics match the gateway's own counters for a recorded run.

## Pros and Cons of the Options

### P1 Separate service

* Good, because independent of the monitored components.
* Bad, because one more container and its own login.

### P2 Console pages

* Good, because login and roles are reused.
* Bad, because the overview fails together with the query-service.

### A1 Admins only

* Good, because system-wide and cost data stay with operators; simple authorization.
* Bad, because project members do not see the overview (they keep the console).

### A2 All users, scoped

* Good, because broader visibility.
* Bad, because every view needs the scope guard and the query-service for login.

### G1 Vendored Cytoscape.js

* Good, because mature, MIT, interactive graphs without a build chain.
* Bad, because some JavaScript glue in templates.

### G2 SPA

* Good, because richest UI.
* Bad, because a third language with a build chain.

### R1 cAdvisor (optional profile)

* Good, because standard metrics without giving the dashboard Docker API access.
* Bad, because privileged read-only host mounts; behavior on Docker Desktop to verify.

### R2 Docker socket in the dashboard

* Good, because no extra container.
* Bad, because socket access is root-equivalent, even when mounted read-only.

## More Information

* Versions to pin in the task (2026-10-06): Cytoscape.js 3.34.x (MIT), uPlot 1.6.x (MIT), cAdvisor v0.60.x (Apache-2.0, to verify).
* To verify: V-36 (Bifrost metrics and budget API), V-37 (cAdvisor on Docker Desktop and Linux), V-38 (Memgraph path queries).
* Related: ADR-0004, ADR-0009, ADR-0011, ADR-0013.
