---
name: printorian-host-layouts
description: "Printorian host layouts: the Windows notebook (backend/.venv/Scripts/python.exe, run_*.bat) and the fastvm Arch VM (backend/.venv/bin/python). 2026-11-17 VM state: Python 3.14 (no 3.13 on the host — constraints.txt resolves cleanly), venv + node_modules installed, rootless podman with fuse-overlayfs, Docker Hub blob CDNs unreachable from this network (ghcr.io and quay.io reachable), PostgreSQL+valkey as native systemd services per the owner's own setup (~/setup-printorian-vm.sh)."
metadata:
  node_type: memory
  type: project
---

**Why:** Every documented command in backend/CLAUDE.md is written for the
Windows layout; running it verbatim on Arch fails in ways that look like
project breakage rather than environment drift. And on this VM the container
route for dev services is dead (network-level), so "just run docker compose"
is advice that cannot work here.

**How to apply:** Detect before running: `backend/.venv/bin/python` (Arch)
vs `backend/.venv/Scripts/python.exe` (Windows). Check services by
connectivity (127.0.0.1:5433 / :6380), never by `docker ps`. On an
unbootstrapped host follow printorian-build-test §8 — or report `COULD NOT
RUN` honestly.

State observed/configured 2026-11-17 on the fastvm Arch VM
(`/home/dimmus/printorian`):

- **Python 3.14.10** is the only interpreter (no `python3.13` command).
  `pip install -e ".[dev]" -c constraints.txt` resolves cleanly on 3.14
  (exit 0, all pins satisfied: SQLAlchemy 2.1.8, asyncpg 0.32.0, numpy
  2.5.4, pydantic 2.14.3). CI uses 3.13 — suite results on 3.14 are
  evidence from a different interpreter and are reported as such.
- venv and `frontend/node_modules` installed; project-level, not system.
- **Rootless podman, kernel lacks the overlay module** —
  `~/.config/containers/storage.conf` pins `mount_program =
  /usr/bin/fuse-overlayfs` (fuse-overlayfs + docker-compose installed via
  pacman); podman user socket enabled, `DOCKER_HOST` exported in
  `~/.bashrc`.
- **Network**: docker.io manifests/tokens work but BOTH blob CDNs
  (production.cloudflare.docker.com, production.cloudfront.docker.com)
  stall — measured with curl (exit 56 after ~20 s), so podman pulls hang at
  the first layer. Mirrors that redirect to those CDNs (docker.m.daocloud.io)
  fail identically. `ghcr.io` (pkg-containers.githubusercontent.com CDN)
  verified end-to-end at ~1 MB/s; `quay.io` reachable; archive.archlinux.org
  lists fine but package downloads crawl (<1 B/s). The owner chose native
  services over registry gymnastics.
- **The owner sets up system packages themselves** ("I want to have all
  tools and packages setup myself with best setup", 2026-11-17) — agents do
  not run pacman. The reviewed setup script lives at
  `~/setup-printorian-vm.sh` (latest postgresql + valkey from extra;
  dedicated cluster `/var/lib/postgres/printorian` on 5433; valkey on 6380;
  role printorian + databases printorian and printorian_test).
- Windows notebook carries the full dev environment;
  `.claude/launch.json` there hardcodes `.venv\Scripts\python.exe` and
  run_*.bat launchers; repo git remote is
  `https://www.github.com/iritur/printorian`.

Related: [[printorian-ports-5433-6380]].
