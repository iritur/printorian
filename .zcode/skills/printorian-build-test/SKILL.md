---
name: printorian-build-test
description: How Printorian is built, run, and tested on this machine — venv layout per host (Arch Linux VM vs Windows notebook), Docker Postgres 5433 / Redis 6380 and the worktree compose rule, the six backend gates with exact commands, pytest on real PostgreSQL (~15 min, strictly serial), Alembic dev-vs-test database traps, the four frontend gates, API-client regeneration, exit-code discipline, fresh-host bootstrap, and known_red handling. Use before running, testing, linting, migrating, or bootstrapping anything in this repo.
---

# Building, running, and testing Printorian

The commands of record live in `backend/CLAUDE.md`, `frontend/CLAUDE.md`,
and `docs/DEVELOPMENT.md`. This skill is the harness's operational copy,
extended with what those files assume (host detection, worktrees) and
nothing they contradict — when in doubt, the CLAUDE.md files win and this
file gets fixed.

## 1. Hosts (binding)

The repo is developed on two layouts; detect, never assume:

| Host | venv python | Detection |
|---|---|---|
| Arch Linux VM | `backend/.venv/bin/python` | `test -x backend/.venv/bin/python` |
| Windows notebook | `backend/.venv/Scripts/python.exe` | `test -f backend/.venv/Scripts/python.exe` |

Everything below writes `$PY` for the resolved interpreter and `$LINT_IMPORTS`
for the venv's `lint-imports` binary (`backend/.venv/bin/lint-imports` or
`backend/.venv/Scripts/lint-imports.exe`). If `backend/.venv` does not exist
at all, the host is not bootstrapped — see §8.

## 2. Services: Postgres 5433, Redis 6380 — what matters is reachability

Both are deliberately off the defaults so a local install is never silently
used instead. The canonical source is the repo root compose file:

```bash
cd /path/to/printorian            # the MAIN checkout
docker compose up -d postgres redis
```

**The compose project (and its published ports) belongs to the main
checkout's directory.** A git worktree has its own `docker-compose.yml`, and
`docker compose up` there creates a *second* project that fails to bind
5433/6380. From a worktree: never run compose; start services from the main
checkout path if needed. The database itself is shared — which is why §4's
serialization rule exists.

**What the gates actually require is that the ports answer** — the workflows
preflight with a TCP connect to `127.0.0.1:5433` and `:6380`, not with
`docker ps`. When the container route is unavailable, host services are the
documented fallback:

- On the fastvm Arch VM (2026-11-17): Docker Hub's blob CDNs
  (`production.cloudflare.docker.com`, `production.cloudfront.docker.com`)
  are unreachable from this network — `docker compose up` can never finish
  there, and registry mirrors that redirect to those CDNs (daocloud) fail
  the same way. `ghcr.io` and `quay.io` ARE reachable (verified), so images
  that live there can still be pulled. The owner's chosen setup: native
  PostgreSQL + valkey as systemd services on 5433/6380, installed by
  `~/setup-printorian-vm.sh` (dedicated cluster `/var/lib/postgres/printorian`,
  unit `printorian-postgres.service`; `valkey.service` with `port 6380`).
  Deviation from the pinned `postgres:17-alpine` is disclosed wherever
  results are reported, never silently absorbed.
- Rootless podman on this VM needs fuse-overlayfs (kernel lacks the overlay
  module): `~/.config/containers/storage.conf` sets `mount_program`; the
  user socket is enabled and `DOCKER_HOST` is exported in `~/.bashrc` for
  docker-compose.

## 3. The six backend gates

Run each separately and read its **own** exit code (§7):

```bash
cd backend
$PY -m ruff check .
$PY -m ruff format --check .
$PY -m mypy                                  # strict
$LINT_IMPORTS                                # architecture contracts
$PY tools/check_context_isolation.py
$PY tools/check_file_length.py               # 400 lines, hard
```

All six run in CI on every push. `alembic check` is the sixth's sibling in
CI and belongs to the migration workflow (§5).

## 4. pytest — real PostgreSQL, strictly serial

```bash
cd backend
PRINTORIAN_ENVIRONMENT=test \
PRINTORIAN_DATABASE_URL="postgresql+asyncpg://printorian:printorian@localhost:5433/printorian" \
$PY -m pytest -q
```

- Must be `python -m pytest`, not bare `pytest` — the pythonpath comes from
  the package invocation.
- ~1100 tests, about **fifteen minutes**. Budget for it rather than
  skipping it (ADR-0021: real PostgreSQL only; there is no SQLite fallback
  and adding one is not a fix).
- **Never two sessions at once** — they share `printorian_test` and truncate
  each other's tables; it has already sent one investigation down a blind
  alley. Before starting, check no other pytest is running.
- Hardware-marked contract tests (`pytest tests/contract/ -m hardware`) need
  a real printer via `PRINTORIAN_HARDWARE`; without one they are
  `UNSUPPORTED`, never silently skipped.
- Scoped runs during implement loops are fine (`$PY -m pytest tests/unit/test_X.py -q`)
  — but a scoped run is never reported as the suite. The full suite runs at
  the baseline and the final gate of a pipeline, not per fix-round.

### Worktrees

A fresh worktree has **no venv** (it is git-ignored) and must not run
compose (§2). Reuse the main checkout's interpreter with the worktree as
cwd — `python -m` puts cwd first on `sys.path`, so the worktree's package
shadows the editable install — and **verify** that before trusting any
result:

```bash
cd /path/to/printorian-wt/<task>/backend
/path/to/printorian/backend/.venv/bin/python -c "import printorian, sys; print(printorian.__file__)"
```

If the printed path is not inside the worktree, the run would test the main
checkout's code: report `COULD NOT RUN` and create a worktree-local venv
(`python3.13 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]" -c constraints.txt`)
instead of quietly proceeding. Frontend worktrees need their own
`npm ci` in `frontend/`.

## 5. Alembic

- Alembic runs against the **dev** database (`printorian` on 5433), never
  the test one. Check `alembic current` before any `downgrade`.
- A migration is finished when: `upgrade head` → `downgrade base` →
  `upgrade head` → `alembic check` all pass, and `tests/test_migrations.py`
  is green (ADR-0008).
- Autogenerate caveats: `USING` clauses for type changes (see migration
  `0005_schema_hardening`), partitioned tables, `order_number_seq`,
  `op.f()`-bound constraint names. Enum widening goes through
  `core.db.enum_column` CHECK constraints with `NOT VALID` / `VALIDATE`.

## 6. Frontend gates and the generated client

```bash
cd frontend
npm run typecheck
npm run lint
npm run test
npm run build
```

Separately when something fails, so the failing command's own exit code is
read. Prettier is **not** a gate; do not reformat files you are not
otherwise editing.

After any backend route or response change (ADR-0005):

```bash
cd backend && $PY tools/export_openapi.py --out openapi.json
cd frontend && npm run generate:api
```

`packages/api-client/src/generated/` is never hand-edited (the hook blocks
it). Regeneration does **not** cover `apps/console/src/dashboard/types.ts`,
which mirrors `GET /dashboard` by hand — change both, or the tile renders
`undefined` while `tsc` stays green. TypeScript major is held below 7
(`openapi-typescript` crashes on the Go rewrite); see
`.github/dependabot.yml` before trying to lift it.

## 7. Exit-code discipline

`cmd | tail` returns *tail's* exit code; a pipe once reported a successful
`npm ci` that had failed, and every check after it ran against stale
`node_modules`. Always:

```bash
cmd > /tmp/out.log 2>&1; echo "exit=$?"
```

then read the log for the counts.

## 8. Fresh-host bootstrap (documented order)

```bash
# 1. services (main checkout)
docker compose up -d postgres redis
# 2. backend
cd backend
python3.13 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]" -c constraints.txt
.venv/bin/alembic upgrade head          # dev DB
# 3. frontend
cd ../frontend && npm ci
# 4. an owner account (optional, needs a real terminal for the password)
cd ../backend && .venv/bin/python tools/provision_owner.py --email owner@yourfarm.ru
```

`printers.local.toml` (real printer credentials) is git-ignored and belongs
to a person, never an agent (ADR-0014).

## 9. known_red handling

A pipeline may carry a `known_red` list — test node ids tolerated as red,
each with its justification and issue number. Rules: a `known_red` test that
goes green is *reported* (the entry is stale, remove it); a *new* red is
never added to the list to make a gate pass — that is weakening, and it
routes through review like any change.
