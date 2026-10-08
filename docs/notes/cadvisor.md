# cAdvisor for the dashboard Resources page (V-37)

Result of T1.11, verified 2026-10-09.

## Image and registry

`gcr.io/cadvisor/cadvisor:v0.60.6` does not exist: the gcr.io repository stops at v0.49.x. The release image is `ghcr.io/google/cadvisor:v0.60.6` (multi-arch, pulled successfully).

## Security

The `resources` profile is opt-in, off by default and not for production. cAdvisor needs read access to host paths (`/`, `/sys`, `/var/lib/docker`) and to container metadata, which is root-equivalent visibility of the host. Mitigations in `deploy/docker-compose.yml`: cAdvisor does not mount the Docker socket; a pinned (digest) `tecnativa/docker-socket-proxy` mounts it read-only and only allows `GET` on `CONTAINERS`, `INFO`, `VERSION`, `PING` (`POST=0`); both run on a dedicated `internal: true` network `resources` shared only with the dashboard (cAdvisor is not on `internal`); `cap_drop: [ALL]` with only `DAC_READ_SEARCH` re-added (without it layer filesystem stats fail), `no-new-privileges`, image pinned by digest, no published ports.

## Image, mounts and flags

`gcr.io/cadvisor/cadvisor:v0.60.6` does not exist (gcr.io stops at v0.49.x); the image is `ghcr.io/google/cadvisor:v0.60.6` (digest pinned). Flags: `--docker=tcp://docker-socket-proxy:2375 --docker_only=true --housekeeping_interval=10s` (default 1 s housekeeping used about one core). The dashboard calls `POST /api/v1.3/docker/` with `{"num_stats": 2}` and maps containers by the label `com.docker.compose.service`, restricted to the Compose project of the cAdvisor container.

## Docker Desktop (macOS, containerd image store)

- Mounting `/var/run` did not expose a Docker socket to cAdvisor; the TCP endpoint through the socket proxy works.
- cAdvisor additionally needs the containerd socket (`/run/containerd`). This is only mounted by the override `deploy/docker-compose.resources-desktop.yml` (`-f docker-compose.yml -f docker-compose.resources-desktop.yml`); the containerd socket is root-equivalent access to the Docker Desktop VM, development use only. Without the override the dashboard shows "not available on this host".
- With the override the Resources page lists all running containers with working set, `mem_limit` and CPU rate. `/dev/kmsg` is missing in the VM, so OOM events are lost.

## Linux

Not run in this session (no Linux host). The base file mounts only what the cAdvisor documentation lists (`/`, `/sys`, `/var/lib/docker`) and uses the socket proxy. Verify with the CI `compose` job extension or on the first Linux host: `docker compose --profile dashboard --profile resources up --wait`, then the Resources page must list every running SANDMAN container. In CI the profile is only validated with `docker compose config` (decision of 2026-10-08); the parser is tested against a recorded cAdvisor document shape.

## Dashboard behavior

- cAdvisor unreachable (profile not started): page says "profile `resources` not active"; nothing else changes and the dashboard health check stays green.
- cAdvisor reachable but without Compose containers (Docker factory not registered): "not available on this host".
- Memgraph memory comes from `SHOW STORAGE INFO` (`memory_res`, `memory_tracked`, `memory_limit` = `--memory-limit`; Memgraph 3.13.1 reports `memory_limit` as `8.00GiB` for `--memory-limit=8192`). The ratio uses `memory_tracked`, the quantity the limit applies to.
- Memory share of 80 % or more is highlighted; Memgraph memory at 80 % of its limit also raises an alert.
