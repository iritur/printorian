# Memory index

One row per record, newest last. Grep this file before proposing around a
rule — the incident that set it usually has a number attached.

- [PG-only tests](printorian-pg-only-tests.md) — ADR-0021: real PostgreSQL only; first run surfaced 66F+68E SQLite had hidden; `printorian_test` is truncated per session, so two concurrent sessions corrupt each other.
- [Alembic traps](printorian-alembic-traps.md) — Alembic targets the dev DB, not the test one; autogenerate misses `USING`, partitions, `order_number_seq`, `op.f()`; downgrade round-trip or it is not finished.
- [API client regen](printorian-api-client-regen.md) — export_openapi + generate:api; the hand-mirrored `apps/console/src/dashboard/types.ts` shipped a broken dashboard with tsc green; TS major held <7 (openapi-typescript crash).
- [Bambu FTPS session reuse](printorian-bambu-ftps-session-reuse.md) — implicit FTPS 990, `ntransfercmd` fix in `tools/bambu_ftps.py`; `DriverStorageError` printers are never available capacity.
- [Silent printer 60 s](printorian-silent-printer-60s.md) — measured 60.1 s per unreachable printer; driver pool `CONNECT_CONCURRENCY=8`, retry 30 s→5 min with jitter; connects in a thread.
- [Backup uid-70 incident](printorian-backup-uid70.md) — 1,385 consecutive silent backup failures, 23 GB un-archived WAL; `pg_stat_archiver` is the heartbeat; host-side backups use `PRINTORIAN_DB_PORT=5433`.
- [Ports 5433/6380](printorian-ports-5433-6380.md) — off-default on purpose; compose project belongs to the main checkout, so worktrees must never `docker compose up` (port conflict).
- [Host layouts](printorian-host-layouts.md) — Windows notebook (`Scripts/python.exe`, .bat) vs fastvm Arch VM; 2026-11-17 VM: py3.14-only, venv+node_modules in, podman+fuse-overlayfs, docker.io blob CDNs unreachable (ghcr/quay fine), owner runs system setup themselves via ~/setup-printorian-vm.sh (native PG+valkey on 5433/6380).
- [Pipe-tail trap](printorian-pipe-tail-trap.md) — `npm ci | tail` once hid a failed install; gate only on `cmd > /tmp/out.log 2>&1; echo "exit=$?"`.
- [Deployment shape ruling](printorian-deployment-shape.md) — owner 2026-11-17: containerized monolith under systemd compose; no microservices; no host installs of the app; CI digest/cosign/release-gate chain is the trust boundary.
- [printorian-server charter](printorian-server-charter.md) — owner 2026-11-17: custom minimal hardened Arch host in its own repo (GitHub + gitverse mirror), custom pacman repo, probe script, minimal kernel, ISO+TUI installer, backup service with drills, web monitoring; Python+TUI tooling; supersedes INFRASTRUCTURE §3's Debian choice.
