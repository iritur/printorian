# Printorian — Infrastructure and delivery

How the system is built, released, run and kept running — by one person, without
that person being the availability mechanism.

Written against what the repository actually contains today, in the same spirit as
[DESIGN-KIT-PLAN.md](DESIGN-KIT-PLAN.md): where something is promised but absent,
that is said plainly rather than folded into a line item.

The product architecture is not up for renegotiation here. [ADR-0001](adr/0001-one-backend-one-database.md),
[ADR-0003](adr/0003-on-prem-backend.md) and [ADR-0016](adr/0016-two-web-apps-no-desktop.md)
fix the topology — one backend, one database, on-premises, with a static storefront
bundle and an `/api` proxy on rented hardware. Everything below serves that shape
rather than arguing with it.

---

## 1. Where infrastructure actually stands

The application is Phase 0–4 mature. The infrastructure around it is Phase 0.

| Concern | Promised where | Reality |
|---|---|---|
| Version control | implicitly everywhere | **Zero commits.** 196 files staged, no `HEAD`, no remote, no branch |
| CI | `.github/workflows/ci.yml`, 6 gates | Well-designed and **has never executed** — there is nothing to push |
| Container images | — | None. `docker-compose.yml` runs *dependencies* only; the app runs from a `.venv` |
| Deployment | ADR-0003 "one on-prem server" | Two Windows `.bat` files that start a dev stack on a laptop |
| Host configuration | RUNBOOK §2, §3 | Prose. Nothing is executable, nothing is idempotent, nothing is reproducible |
| The tunnel | ADR-0016 "reverse-proxies `/api` back to the farm" | Undesigned and unbuilt |
| Secrets | ADR-0014, ADR-0019 escrow | Environment variables and a gitignored `printers.local.toml` |
| Backups | ADR-0019, `scripts/backup.sh` | Scripts exist and are good. **Nothing schedules them.** No off-site destination |
| Restore drill | ADR-0019 "a failing drill is an incident" | `scripts/restore_drill.py` exists. Nothing runs it; nothing would notice a failure |
| Observability | ARCHITECTURE §10: "Prometheus metrics, `/health` covering DB, Redis and each driver" | Zero metrics. `/health/ready` checks the database only — not Redis, not drivers |
| Alerting | — | None. The only monitor is a person looking at a screen |

Two of these deserve to be named as more than gaps.

**Nothing is committed.** Every gate in `docs/DEVELOPMENT.md`, every contract in
`.importlinter`, the entire six-gate CI pipeline — all of it is theory until a
commit exists and a remote receives it. A hard-drive failure this afternoon
destroys the whole system, and there is no evidence that CI passes at all. This is
the only item in this document with no acceptable delay.

**ARCHITECTURE §10 overstates what runs.** By the repository's own rule — *status
docs say works / scaffolded / stubbed* — the observability row is **stubbed**: the
structured logging is real and good, the metrics and the dependency-aware health
check are not. That table should be corrected in the same change that fixes it.

---

## 2. The three requests, answered honestly

The brief asked for IaC, CI/CD, microservices and containers. Three of those are
straightforwardly right for this system. One is not, and saying so is the more
useful answer than complying.

### Containers — yes, and for a specific reason

Not for density or scaling. For **one reproducible artifact**. Today the runtime is
"whatever `pip install -e .` resolved on the machine that ran it last", which is a
different set of versions on the laptop and the farm, and no way to tell which. A
digest-pinned image makes the thing that was tested and the thing that runs
provably identical, and makes rollback a one-word operation.

### IaC — yes, split across two tools by domain

The two halves of this system are different kinds of thing and want different
tools. Using one for both is the usual mistake.

| Domain | Tool | Why |
|---|---|---|
| Rented VPS, DNS, off-site bucket, GitHub repo settings | **OpenTofu** | These have APIs and a lifecycle. Declarative state is exactly right |
| The farm's physical box | **Ansible** | Hardware nobody provisions. What is needed is *convergence* — packages, mounts, units, firewall — not creation |

OpenTofu rather than Terraform: drop-in, and it removes a licence question from
infrastructure a solo operator will still be running in five years.

Terraforming the **GitHub repository itself** (branch protection, required checks,
environments, Renovate config) is worth the twenty lines. It is the difference
between "the gates are configured" and "the gates are configured, and I can prove
what they were on the day that shipped".

### CI/CD — yes, and pull-based

CI already exists and is better than most. What is missing is CD, and the farm's
topology dictates its shape: **the farm has no inbound connectivity, so nothing can
push to it.** The farm pulls. See §6.

### Microservices — no, and the reason is the interesting part

[ADR-0001](adr/0001-one-backend-one-database.md) already forbids splitting the
domain, and it is right. Worth adding is *why this is the modern answer rather than
the conservative one*.

The properties people want from microservices are independent deployability, fault
isolation, replaceable parts, and boundaries that do not erode. This codebase
already has all four, bought statically instead of over a network:

* `import-linter` contracts and `check_context_isolation.py` enforce context
  boundaries **at build time**. A network boundary enforces the same thing at
  runtime, less reliably, and only after you have deployed.
* `drivers/` is a registry behind a brand-neutral interface ([ADR-0011](adr/0011-brand-neutral-driver-interface.md)).
  A new printer brand is one module. No service was needed to get that.
* The workers already run as a **separate process** from the API, for a real reason
  (one SLA clock, not one per API worker). That is the split that was actually
  justified, and it was taken.
* Pricing is a pure function ([ADR-0002](adr/0002-pricing-is-a-pure-function.md)).
  It is more isolated than any service could make it, because it cannot reach
  anything at all.

What splitting would cost here is specific, not vague. **The release unit of this
system is the commit, not the service** — [ADR-0005](adr/0005-generated-api-client.md)
generates the TypeScript client from the backend's schema at the same commit, so
backend and frontend that disagree cannot both be current. Independent deployment
of two halves of one contract is not a feature; it is the drift ADR-0005 exists to
prevent. Add to that: one box (ADR-0003), one database (ADR-0001), no independent
scaling pressure, and one maintainer. Every service added multiplies deploy
pipelines, health checks, failure modes and 3 a.m. ambiguity by N, and divides
nothing.

**Where extraction is genuinely justified, and how to keep it cheap.** The plan
below preserves four seams, so this decision stays reversible:

| Seam | Status | Extract when |
|---|---|---|
| **Edge** (bundle + `/api` proxy) | Already separate (ADR-0016) | Done. This is the one real split |
| **Workers** | Already a separate process, will be a separate container | Never needs to leave the box; can scale to N replicas in place |
| **Mesh analysis / slicing** | In-process (`contexts/catalog`, numpy) | CPU contention with request handling becomes measurable. Naturally a queue-fed worker — the seam is a job type, not a rewrite |
| **A second site** | Out of scope (ADR-0003) | A second farm opens. That is a new ADR and a genuine distributed decision, not a refactor to anticipate now |

The infrastructure below deploys a *set of images from one commit*. Making that set
larger than two is a configuration change, not an architecture change. That is what
extensibility means here.

---

## 3. Target topology

```
                    ┌─────────── GitHub ───────────┐
                    │  source · CI gates · GHCR    │
                    │  images signed (cosign/OIDC) │
                    └───────┬──────────────┬───────┘
                            │ pull         │ pull
        ┌───────────────────▼──────┐       │
        │  EDGE — rented VPS (RU)  │       │
        │  Caddy: TLS, static      │       │
        │   storefront bundle      │       │
        │   /api → WireGuard       │       │
        │  offline page on 502     │       │
        │  dead-man's switch       │       │
        └───────────┬──────────────┘       │
                    │ WireGuard (farm dials out)
  ══════════════════╪═══════════════ farm LAN ══════════════════
                    │                      │
        ┌───────────▼──────────────────────▼───────────────────┐
        │  FARM HOST — Debian, Docker Compose under systemd    │
        │                                                      │
        │   caddy ──── console bundle (LAN) + /api             │
        │   api        (image :sha, N replicas)                │
        │   workers    (same image, `python -m ...workers`)    │
        │   postgres 17 ── data disk                           │
        │   redis                                              │
        │   victoriametrics · grafana · alertmanager · loki    │
        │   postgres_exporter · node_exporter                  │
        │                                                      │
        │   systemd timers: deploy · backup · drill · reboot   │
        │   volumes: /srv/printorian/{storage,pgdata}          │
        │            /mnt/backup  ← separate physical disk     │
        └──────────────┬───────────────────────────┬───────────┘
                       │ MQTT/TLS:8883, FTPS:990   │ HTTPS
                       ▼                           ▼
                 Bambu printers            staff browsers,
                 (own VLAN)                kiosk wall display

   off-site: restic → RU object storage (encrypted, key escrowed offline)
```

Everything on the farm host is one `docker-compose.yml` under one systemd unit. The
farm's OS is **Debian stable or Ubuntu LTS** — WAL archiving, systemd timers, UPS
integration and unattended upgrades all assume it, and running the farm on Windows
because the development machine is Windows would be a decision made for the wrong
reason.

---

## 4. Decisions, with what was rejected

Each of these becomes an ADR when its stage lands (§10).

### 4.1 Orchestration: Compose under systemd, not Kubernetes

Kubernetes buys scheduling across nodes, rolling deploys, and self-healing. There
is **one node**, and the database is a stateful singleton that would be pinned to
it regardless. Self-healing on one host is `restart: unless-stopped` plus a
healthcheck. What it costs is a control plane to patch, a CNI, storage classes, an
ingress controller and a second configuration language — for one person that is the
difference between infrastructure that runs itself and infrastructure that *is* the
job.

*Escape hatch:* if a second node ever appears, k3s takes these compose files with a
`kompose`-shaped translation and no application change. The reason that is cheap is
the same reason the monolith is cheap — one image, one config surface.

*Considered:* **Podman + Quadlet** — rootless, daemonless, systemd-native, and
arguably the better long-term answer. Rejected for now only because the development
machine runs Docker Desktop and a second container runtime is a divergence between
dev and prod, which is the exact thing containers are here to remove. Revisit if
rootless becomes a requirement.

### 4.2 One image, many commands

`ghcr.io/<owner>/printorian-backend:<sha>` runs the API, the workers, and the
migrations — differing only by command. One thing to build, scan, sign, cache and
roll back. A second image, `printorian-console:<sha>`, is Caddy plus the built
console bundle; the storefront bundle ships to the edge as a tarball artifact from
the same commit.

**All artifacts from one commit deploy as a set**, because ADR-0005 makes them one
contract. The release unit is the commit SHA.

### 4.3 Edge: RU VPS + WireGuard + Caddy

This is the one decision that needs your input rather than mine, because it is
legal and commercial before it is technical.

| Option | For | Against |
|---|---|---|
| **RU VPS + WireGuard + Caddy** (recommended) | Data stays in Russian jurisdiction; no third party terminates TLS for a shop taking ₽ through YooKassa; nothing about it is proprietary; low latency to RU customers | A host to patch (Ansible handles it); ~300–700 ₽/month |
| **Cloudflare Tunnel** | Nothing to run or patch, DDoS absorbed, free tier, no VPS at all | A foreign intermediary terminates TLS for a Russian e-commerce site. 152-ФЗ and 54-ФЗ make that a question for a lawyer, not an architect. Vendor lock-in on the ingress path |
| **Backend in the cloud + on-prem agent** | Storefront survives an uplink outage | Precisely the distributed-system layer ADR-0003 exists to reject, and it puts the farm's finances on rented hardware |

Recommended: **VPS + WireGuard**. The farm dials out, so no port forwarding, no
static IP, and no inbound attack surface on the box holding the business.

**The honest degradation matters.** ADR-0003 already accepts that the storefront is
unreachable while the uplink is down. Caddy should serve a static, styled *"the farm
is temporarily offline"* page on upstream failure rather than a 502 — and the edge
should alert when it starts doing so. Cheap, and it is the difference between a
known outage and a mystery.

### 4.4 Secrets: SOPS + age

Encrypted `secrets.enc.yaml` files committed to the repository; the age private key
lives only on each host, placed once by Ansible from your keyring and never in git.
Rotation is an edit and a commit. Diffs stay reviewable because SOPS encrypts values
and not keys.

*Rejected:* **HashiCorp Vault** — a second production system to run, unseal, back up
and patch. For one operator that is a net loss in availability.

Per [ADR-0019](adr/0019-backup-is-wal-archived-and-drilled.md), `PRINTORIAN_SECRET_KEY`
is **escrowed separately from the backups** — a password manager plus a printed copy
off-site. A restore without it returns every order and a fleet nobody can drive.

### 4.5 Expand/contract migrations

New, and load-bearing for rollback. Today a deploy that goes wrong can only be
rolled back if the migration it carried is reversible *and* the old code tolerates
the new schema. Adopting expand/contract — additive migration, deploy, backfill,
deploy, contract in a later release — means **rolling back the application never
requires rolling back the database.** [ADR-0008](adr/0008-alembic-only.md) already
tests downgrades, which makes this an operating rule rather than new machinery.

---

## 5. What "autonomous" means concretely

Autonomy is not a property of a stack; it is a set of closed control loops. Each one
below detects a specific failure and does something about it without you.

| Loop | Detects | Response |
|---|---|---|
| Container health | Process death, deadlock, unready dependency | `restart: unless-stopped` + healthcheck; API is only in the proxy's upstream pool once `/health/ready` passes |
| Deploy gate | A release that starts but does not become ready | Automatic rollback to the previous digest, then alert |
| Backup | Dump missing, empty, or old | `printorian_backup_last_success_timestamp` older than 26 h → alert |
| Restore drill | A backup that has been quietly useless for weeks | Weekly `restore_drill.py`; failure is a **page**, per ADR-0019 |
| WAL archive | `archive_command` failing, filling the data disk | `pg_stat_archiver` failure count > 0 → alert *before* the disk fills |
| Disk | Data, backup or storage volume filling | 80 % warn, 90 % page |
| Fleet | A printer offline, a job stuck in `dispatching` | Domain metrics → alert. ADR-0007 guarantees offline is real, never fabricated |
| Partitions | The maintenance sweep failing silently — the case `config.py` already names | `telemetry_partition_months_ahead` gauge < 1 → alert |
| Dependencies | Security drift in Python/npm/base images | Renovate PRs; patch and dev updates auto-merge when all six gates are green |
| OS patching | Unpatched kernel/openssl | `unattended-upgrades`, reboot in a window — **deferred while any job is printing** |
| Power | Mains loss | UPS + `nut` → clean shutdown before the battery dies |
| **The host itself dying** | Everything above, including the monitoring stack | **External dead-man's switch** — see below |

**The dead-man's switch is the one that makes the rest trustworthy.** Prometheus,
Grafana and Alertmanager run on the farm host, so they cannot tell you the farm host
is gone. The farm pushes a heartbeat every minute to a watcher on the VPS (or
healthchecks.io); the watcher alerts when it stops. Without this, the most likely
total failure is also the most silent one.

**Alert discipline.** For a single maintainer, alert fatigue is the failure mode
that kills the whole system. Only conditions requiring a human *within the hour*
page. Everything else lands in a daily digest. If an alert fires and the correct
response is "nothing", it is deleted or turned into a dashboard the same week.

Routing: Alertmanager → Telegram bot (free, on the phone, works in Russia) with
a secondary email path.

### The metrics that matter here are domain metrics

Request rates and CPU graphs will not tell you the farm has stopped earning. These
will, and every one maps to a decision already written down:

```
printorian_printers_offline{printer,brand}              ADR-0007
printorian_job_stuck_seconds{state}                     jobs wedged in dispatching
printorian_backup_last_success_timestamp                ADR-0019
printorian_restore_drill_last_success_timestamp         ADR-0019 — a failing drill is an incident
printorian_wal_archive_failures_total                   docker-compose archive_command
printorian_telemetry_partition_months_ahead             config.py names this failure explicitly
printorian_estimate_variance_ratio                      ADR-0013 tolerance band
printorian_orders_awaiting_prep                         the human queue backing up
printorian_payment_notifications_unreconciled           ADR-0019's reason for a 1-minute RPO
printorian_sla_credit_accrued_rub                       money leaking through lateness
```

### Tracing: deliberately not yet

OpenTelemetry earns its keep across service boundaries. There is one process, one
database, and a `correlation_id` already threaded through every structlog line. Ship
logs to Loki so that id becomes searchable, and add OTel on the day a second
deployable exists — not before. Instrumenting a monolith with distributed tracing is
the most common way a small team spends a month buying a dashboard nobody opens.

---

## 6. Delivery pipeline

```
  PR ──► the six gates (unchanged) ──► merge
                                        │
  main ──► build images (buildx, cached, digest-pinned bases)
        ──► SBOM (syft) · scan (trivy, fail on fixable HIGH/CRITICAL)
        ──► sign (cosign, keyless via GitHub OIDC)
        ──► push GHCR :sha-<short>
                                        │
        ──► RELEASE GATE ── compose up the *built image*:
              alembic upgrade head → downgrade base → upgrade head
              /health/ready green
              virtual-farm E2E against the image
                                        │
  tag v* ──► promote: write the digest into deploy/production.yaml, commit
                                        │
  farm timer (2 min) ──► git pull ──► digest changed?
              ──► cosign verify ──► compose pull ──► up -d --wait
              ──► /health/ready within 90 s?
                       yes ──► done, record digest
                       no  ──► restore previous digest, alert
```

Four things about this are deliberate.

**The release gate is new and is the biggest single correctness win.** Today CI
tests the *source tree*. It does not test the artifact — a missing system library,
a wrong entrypoint, a migration that only works with a dev dependency present, all
ship undetected. Running the built image through migrate-up, migrate-down,
migrate-up, readiness and the virtual farm closes that gap for the price of one job.

**Deployment is a commit.** `deploy/production.yaml` holds image digests; changing
it *is* the deploy, reverting it *is* the rollback, and `git log` on that file is
the deployment history — for free, and correct by construction.

**The farm verifies signatures before running.** `cosign verify` on the farm closes
the "someone with a GHCR token pushed an image" hole. Pull-based delivery without
verification just moves the trust, it does not establish it.

**Promotion is a tag, not a merge.** Merging to `main` builds and proves an image;
tagging promotes it. That separation is what makes it safe to merge on a Friday.

### Environments

| Environment | Runs where | Purpose |
|---|---|---|
| `local` | Compose on the dev machine | Unchanged from today |
| `test` | CI, ephemeral | The six gates, unchanged |
| `release` | CI, ephemeral, **the built image** | The gate above |
| `staging` | Compose profile on the farm host, own DB, `mock` driver | Where Phase 2's open item — YooKassa exercised against test-mode credentials — lives permanently instead of being a one-off |
| `production` | The farm host | — |

Per-PR preview environments are correct for a team and wrong here: N ephemeral
stacks to maintain for one reviewer who is also the author.

---

## 7. Implementation plan

Staged so each stage is independently valuable and independently abandonable.
Estimates are for one person working part-time; Stages 0–2 deliver roughly
four-fifths of the value.

### Stage 0 — Version control · **today, 1 hour**

Nothing else in this document is meaningful until this is done.

1. `git commit` the staged tree. Review `git status` first — `.claude/`, the
   `backend/var/storage` contents and `backend/printers.local.toml` must be
   excluded, and eleven untracked migrations plus a dozen untracked routers are
   currently *outside* the index and would be lost.
2. Extend `.gitignore`: `.claude/`, `backend/var/`, `backend/.import_linter_cache/`,
   `data/qr.png` if generated.
3. Private GitHub repository, push, watch CI run for the first time. Expect it to
   fail; the gates have never been executed.
4. Branch protection on `main`: all six gates required, linear history.

**Exit criterion:** a green CI run exists on a remote, and the working tree can be
reconstructed on another machine from `git clone` alone.

### Stage 1 — The artifact · **done**

* [`backend/Dockerfile`](../backend/Dockerfile) — multi-stage, `python:3.13-slim`
  pinned by digest, non-root uid 10001, no build toolchain in the runtime layer.
  One image, three commands (§4.2). 460 MB.
* [`frontend/Dockerfile`](../frontend/Dockerfile) — context is the **repository
  root**, because the API client is generated from the backend's schema *inside
  the build* (ADR-0005). Targets: `console` (Caddy + bundle) and `web-dist` (the
  storefront bundle alone, for the edge).
* [`deploy/compose.prod.yml`](../deploy/compose.prod.yml) — postgres · redis ·
  migrate · api · workers · console. PostgreSQL tuning parameterised against
  RUNBOOK §3 rather than hardcoded, because the right value is a property of the
  box.
* [`deploy/console.Caddyfile`](../deploy/console.Caddyfile) — serves the SPA and
  proxies `/api` same-origin, stripping the prefix exactly as the Vite dev proxy
  does.
* CI `image` job: buildx with layer cache, the release gate, trivy, SBOM, GHCR
  push and keyless cosign signing — push and sign only from `main`.

**Exit criterion met.** The stack comes up from built images with `--wait`
returning 0 and every service healthy; `alembic downgrade base` → `upgrade head`
→ `check` round-trips inside the image; `/health/ready` reports `database: ok`;
the console serves its bundle and proxies `/api/health` on one origin; every
worker loop reports itself sweeping; and a live event published in one container
arrives in the other (`backend/tools/relay_probe.py`) — the hop the console's
boards depend on, and one that only this arrangement can prove.

Two things this stage found, which is the point of it:

* The image's HTTP healthcheck is inherited by all three commands, so the
  **workers container reported unhealthy while sweeping correctly**. Left in
  place, Stage 4's deploy gate would have rolled back good releases. Disabled
  rather than replaced with a process check — a signal that cannot tell working
  from wedged is the objection ADR-0007 raises against a driver returning
  plausible data.

  **Now replaced rather than disabled.** Each loop records a beat in Redis at the
  *end* of every pass (`backend/printorian/core/heartbeat.py`), with an expiry
  derived from that loop's own interval, and
  `python -m printorian.workers --check` fails when any of them has stopped
  beating. That is the distinction the process check could not make: a loop
  blocked on a lock or throwing every iteration stops beating while its process
  stays alive. The release gate asserts it by name, so a wedged sweep fails the
  gate rather than shipping. Worth knowing: it reports unhealthy when *Redis* is
  unreachable, because a worker that cannot report is not a worker known to be
  working — two conditions, one signal, and the honest reading of both.
* The suite passed locally for a reason unrelated to the code: `python -m pytest`
  puts `backend/` on `sys.path`, the bare `pytest` console script CI runs does
  not. Fixed as `pythonpath` in `pyproject.toml`.

### Stage 2 — The farm host as code · **~1 week**

Ansible role `printorian-farm`, idempotent, re-runnable:

* OS baseline: Debian stable, non-root deploy user, SSH keys only, `nftables`
  (LAN + WireGuard only), `unattended-upgrades`, journald limits, NTP.
* Disks: `/srv/printorian` (data) and `/mnt/backup` on a **different physical disk**
  — ADR-0019 requires it and the compose default violates it.
* Docker, the compose stack, one `printorian.service` systemd unit.
* SOPS/age key placement; the `.env` rendered from the encrypted store.
* systemd timers: `printorian-backup.timer` (nightly, `scripts/backup.sh`),
  `printorian-drill.timer` (weekly, `scripts/restore_drill.py`),
  `printorian-deploy.timer` (§6).
* Off-site: `restic` to RU object storage, **blobs before the database** per
  ADR-0019's ordering rule, repository password escrowed separately.
* UPS via `nut`, with a clean shutdown hook.
* The reboot guard: no unattended reboot while any job is `printing`.

**Exit criterion:** a wiped machine reaches a running production farm from
`ansible-playbook` plus one SOPS key, and a restored backup passes the drill on it.

### Stage 3 — The edge · **~4 days**

* OpenTofu: VPS, DNS, the off-site bucket, GitHub repo settings, remote state.
* Ansible role `printorian-edge`: Caddy (automatic TLS), WireGuard peer, the
  storefront bundle under an atomically-swapped symlink, the offline page, security
  headers, the storefront's static-asset cache policy.
* Farm-side WireGuard, dialling out.
* Heartbeat receiver + dead-man's switch (§5).

**Exit criterion:** the storefront serves over HTTPS on the real domain, `/api`
reaches the farm, the console remains LAN-only, and pulling the farm's uplink
produces the offline page and an alert within two minutes.

### Stage 4 — Continuous deployment · **~3 days**

* `deploy/production.yaml` (digests) and `deploy/staging.yaml`.
* `printorian-deploy.timer`: pull → verify signature → migrate → up → health gate →
  rollback on failure → report.
* The promotion workflow on `v*` tags.
* Runbook: deploy, rollback, and the expand/contract rule (§4.5).

**Exit criterion:** a tagged commit reaches the farm with no human action, and an
image deliberately made unhealthy rolls itself back and alerts.

### Stage 5 — Observability and autonomy · **~1 week**

Part application work, part infrastructure — the application half is the reason
ARCHITECTURE §10 is currently overstated.

* `prometheus-fastapi-instrumentator` on the API; a `/metrics` endpoint on the
  workers; the domain collectors listed in §5.
* Fix `/health/ready` to cover **Redis and each driver's connection state**, as
  §10 already claims.
* VictoriaMetrics (single-node — lighter and better-compressing than Prometheus on
  one box), Grafana, Alertmanager, Loki + Alloy for the structlog JSON stream.
* Three dashboards, no more: *Farm* (printers, jobs, queue), *Money* (orders, SLA
  credits, payment reconciliation), *Machine* (disk, WAL, backups, containers).
* Alert rules and Telegram routing, written to the discipline in §5.

**Exit criterion:** killing a printer, filling a disk, and corrupting a backup each
produce exactly one correct alert on the phone within five minutes — and normal
operation produces none.

### Stage 6 — Supply-chain autonomy · **~2 days**

* Renovate: grouped, scheduled weekly, **auto-merge for patch and dev updates when
  all six gates pass**; majors open a PR and wait.
* `pip-audit` and `npm audit` as CI gates; CodeQL on a schedule.
* Base-image digest bumps via Renovate.
* The `staging` compose profile, and the YooKassa test-mode proof pinned to it.

**Exit criterion:** a week passes in which dependency updates land, tested, without
you having touched anything.

### Stage 7 — Proving it · **~1 week, and this closes ROADMAP Phase 7**

* **Bare-metal DR drill**: rebuild the entire farm on a fresh VM from git + Ansible
  + off-site backup, timed. Publish the measured RTO instead of an assumed one.
* Load test at realistic printer and order counts; establish the MQTT connection
  ceiling ROADMAP Phase 7 asks for.
* Security review of the tunnel, the upload path and the payment flow.
* Runbooks written **from the drills**, not from speculation.
* Write the ADRs (§10) and correct ARCHITECTURE §10.

**Exit criterion:** a full rebuild from nothing but the git remote, the off-site
backup and the escrowed key succeeds on unfamiliar hardware, within a stated and
measured RTO.

---

## 8. Recovery objectives

| Scenario | RPO | RTO target | Mechanism |
|---|---|---|---|
| Container crash | 0 | seconds | Restart policy + healthcheck |
| Bad release | 0 | < 3 min | Digest rollback, automatic |
| Database corruption | ~1 min | < 1 h | WAL + base backup, PITR |
| Data disk loss | ~1 min | < 4 h | Base backup + WAL from `/mnt/backup` |
| Total host loss | ≤ 24 h (blobs), ~1 min (DB, if `/mnt/backup` survives) | < 8 h | Ansible + off-site restic + escrowed key |
| Farm destroyed | ≤ 24 h | 1–2 days | Off-site only; new hardware is the long pole |
| Uplink down | n/a | n/a | Accepted (ADR-0003). Printers keep printing; the shop stops taking orders |

The single-box SPOF is deliberate and stays. A streaming replica on a second LAN box
is the obvious next increment if the measured RTO proves unacceptable — but it adds
failure modes (replication lag, split-brain on failback) that a solo operator pays
for daily against an outage that may never come. **Measure the RTO in Stage 7
first, then decide.** Not before.

---

## 9. Cost

| Item | Monthly |
|---|---|
| RU VPS (2 vCPU / 4 GB) | 300–700 ₽ |
| Domain | ~100 ₽ |
| Off-site object storage (~100 GB) | 150–300 ₽ |
| GitHub (private repo, Actions, GHCR) | 0 ₽ on the free tier at this volume |
| Monitoring, alerting | 0 ₽ — self-hosted; Telegram is free |
| UPS | one-time, 8–15 000 ₽ |
| **Running total** | **≈ 600–1 100 ₽/month** |

Deliberately near-zero recurring cost, because a solo-operated system that costs
real money per month acquires a reason to be cut, and gets cut on the wrong week.

---

## 10. The ADRs this implies

Written when the corresponding stage lands, following the repository's convention
of recording accepted decisions rather than intentions.

| ADR | Decision |
|---|---|
| 0022 | Containers are the deployment unit; one image, many commands |
| 0023 | Compose under systemd on one host — Kubernetes is rejected, k3s is the escape hatch |
| 0024 | IaC is split: OpenTofu for what has an API, Ansible for the farm's box |
| 0025 | Delivery is pull-based and signature-verified; deployment is a commit |
| 0026 | Secrets are SOPS + age; Vault is rejected |
| 0027 | The edge is a RU VPS over WireGuard; Cloudflare is rejected on jurisdiction |
| 0028 | Migrations are expand/contract, so an application rollback never needs a schema rollback |
| 0029 | Autonomy is control loops with an external dead-man's switch, not a dashboard |

---

## 11. What this plan deliberately does not do

Named so that their absence reads as a decision rather than an oversight.

* **No Kubernetes, no service mesh, no Argo.** One node. §4.1.
* **No distributed tracing yet.** One process. §5.
* **No microservice extraction.** §2, with the seams that keep it cheap.
* **No HA database.** §8 — measure the RTO before buying a daily cost against a
  rare event.
* **No per-PR preview environments.** One reviewer, who is the author.
* **No self-hosted CI runners.** GitHub-hosted is free at this volume and is one
  fewer machine to patch.
* **No blue/green or canary.** Meaningless at one instance behind one proxy; the
  health-gated rollback in §6 delivers what they would.
