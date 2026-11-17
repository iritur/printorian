---
name: printorian-ports-5433-6380
description: "Dev Postgres is on 5433 and Redis on 6380, off the defaults on purpose so a local install is never silently used; corollary discovered 2026-11-17: the compose project (and its ports) belongs to the main checkout's directory — docker compose from a git worktree creates a second project that fails to bind."
metadata:
  node_type: memory
  type: project
---

**Why:** The off-default ports exist so that a developer-machine PostgreSQL
or Redis is never accidentally tested against ("so a local install of
either is not silently used instead" — backend/CLAUDE.md). The worktree
corollary exists because compose derives its project name from the
directory: two checkouts means two projects claiming the same ports.

**How to apply:** Services come from the MAIN checkout:
`cd <main>/printorian && docker compose up -d postgres redis`. From a
worktree, never `docker compose up` — check `docker ps` and, if needed,
start services by pointing at the main checkout. Worktree tests reuse the
main venv with cwd = worktree (verify `import printorian` resolves inside
the worktree first — see the build-test skill §4).

Evidence: backend/CLAUDE.md (port note), docker-compose.yml at repo root.
Corollary derived 2026-11-17 when the harness introduced worktrees
(`~/printorian-wt/<task>`); no incident yet — keep it that way.

Related: [[printorian-pg-only-tests]], [[printorian-host-layouts]].
