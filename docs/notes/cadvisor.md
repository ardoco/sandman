# cAdvisor for the dashboard Resources page (V-37)

Result of T1.11, verified 2026-10-09.

## Image and registry

`gcr.io/cadvisor/cadvisor:v0.60.6` does not exist: the gcr.io repository stops at v0.49.x. The release image is `ghcr.io/google/cadvisor:v0.60.6` (multi-arch, pulled successfully).

## Mounts and flags (profile `resources`)

Read-only: `/` (as `/rootfs`), `/var/run/docker.sock`, `/run/containerd`, `/sys`, `/var/lib/docker`. Flags: `--docker_only=true --housekeeping_interval=10s` (the default 1 s housekeeping used about one core). The container is on the `internal` network only and publishes no port; the dashboard calls `POST /api/v1.3/docker/` with `{"num_stats": 2}` (the default would return 60+ samples per container, about 0.5 MB for 13 containers even with two) and maps containers by the label `com.docker.compose.service`, restricted to the Compose project of the cAdvisor container itself.

## Docker Desktop (macOS, engine 29, containerd image store)

- With the originally planned mount `/var/run:/var/run:ro` cAdvisor did not find a Docker socket (`/var/run/docker.sock: no such file`), registered no Docker factory and reported no containers. The dashboard then shows "not available on this host".
- With `/var/run/docker.sock` mounted instead, cAdvisor additionally needed `/run/containerd` (containerd client for the containerd image store). With both mounts the Docker factory registers and the Resources page lists all 13 running containers of the stack with working set, `mem_limit` (as `spec.memory.limit`) and CPU rate.
- Not available on Docker Desktop: OOM events (`/dev/kmsg` is missing in the VM; harmless, only the OOM metric is lost) and the machine id warnings.

## Linux

Not run in this session (no Linux host). The mounts are the ones from the cAdvisor documentation (`/`, `/var/run` or the socket, `/sys`, `/var/lib/docker`); `/run/containerd` is created empty on the host if it does not exist. Verify with the CI `compose` job extension or on the first Linux host: `docker compose --profile dashboard --profile resources up --wait`, then the Resources page must list every running SANDMAN container. In CI the profile is only validated with `docker compose config` (decision of 2026-10-08); the parser is tested against a recorded cAdvisor document shape.

## Dashboard behavior

- cAdvisor unreachable (profile not started): page says "profile `resources` not active"; nothing else changes and the dashboard health check stays green.
- cAdvisor reachable but without Compose containers (Docker factory not registered): "not available on this host".
- Memgraph memory comes from `SHOW STORAGE INFO` (`memory_res`, `memory_tracked`, `memory_limit` = `--memory-limit`; Memgraph 3.13.1 reports `memory_limit` as `8.00GiB` for `--memory-limit=8192`). The ratio uses `memory_tracked`, the quantity the limit applies to.
- Memory share of 80 % or more is highlighted; Memgraph memory at 80 % of its limit also raises an alert.
