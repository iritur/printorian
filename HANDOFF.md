# Handoff

Where the work stands, what is deliberately unfinished, and what needs a person.
Standing rules are in [CLAUDE.md](CLAUDE.md); this file is the part that changes.

**Update this before finishing a session.** A stale handoff is worse than none —
it is read as current, and this repository has already been bitten twice by
status documents that described built features as missing.

**As of:** 2026-08-23 · 1188 backend tests passing (6 hardware tests skip without
a printer), 206 frontend, all six governance gates green.

**The system now runs on a real host.** A farm exists at `192.168.29.148`
(Ubuntu 26.04, VMware), in production mode, and getting it there is what most of
§1 is about — deploying it found nine defects that all six gates and 1174 tests
had passed over, four of them in units committed hours earlier the same day.

---

## 1. What landed recently, and why it matters

Read this section before touching the fleet, the dashboard, pricing or the
stylesheets — each item changes something a person will notice.

**The farm can change its own pricing rates.** `contexts/settings` is a key/value
store with an audit, serving the seventeen scalar rates through
`GET/PUT/DELETE /settings`, gated on `MANAGE_PRICING` (owner only). Two properties
are load-bearing and both are tested:

> A key with no row resolves to the **code default**, so an empty table prices
> exactly as the farm always did — nothing is seeded, and the migration moves no
> prices on the day it runs. And an order keeps the rate snapshot it was agreed at
> (ADR-0020), so raising a margin changes the next quote and nothing already sold.

The catalogue is derived from `dataclasses.fields(RateSnapshot)` rather than
hand-listed, so a rate added later appears in the screen without a second place to
remember. The other ~85 kit parameters are still constants on `core.config.Settings`
and are a bigger job than they look: they are read once at process start, so moving
them changes *when* they are read as well as where from.

`pricing.py` reached the 400-line gate and split — spec assembly moved to
`_pricing_spec.py`, and with it the mesh-analysis cache.

**Telemetry is summarised, and retention is on.** `metric_rollups` holds one row
per printer per hour. `telemetry_retention_days` ships at 90 for the first time —
it had been 0 since the table existed, because dropping raw samples with nothing
summarising them destroys the only copy.

> The safety property is a **clamp**, not an ordering. The drop cutoff is
> `min(now − retention, watermark)`, where the watermark is the hour rollups have
> actually reached. A farm whose summarising stalls stops dropping raw samples
> with it, and one that has never summarised an hour drops nothing at all. If you
> touch `workers/maintenance.py` or `contexts/fleet/retention.py`, this is the
> invariant to preserve — it is the only irreversible path in the system.

**The dashboard's occupancy figures changed source, and some numbers moved.**
`run_hours` / `capacity_hours` / `idle_hours` and the 7 × 24 load map used to be
derived from `print_jobs` — booked time, not running time. A paused or errored
print counted as run time; a job row never closed counted from its start to `now`
for ever; machine time with no job behind it was invisible; idle was a residual
against the roster. They are now measured from `metric_rollups`, and the
job-derived versions were deleted rather than left alongside.

Two consequences are visible on purpose: the note reads «ИЗ N ИЗМЕРЕННЫХ» rather
than «ВОЗМОЖНЫХ», and an unmeasured window shows an em dash where it used to show
`0.0`. On the load map, an hour nobody polled is **hatched** — distinct from both
bright and the outlined zero that means "measured, and idle".

**`GET /fleet/metrics` and `/fleet/metrics/{printer_id}` serve it.** Seconds only;
money and energy stay behind `VIEW_FINANCIALS`. Deliberately two routes with
different shapes, under their own prefix — `/printers/metrics` would collide with
`/printers/{printer_id}` by declaration order.

**The legacy `--pr-*` design tokens are gone**, along with 119 dead CSS rules.
Reachability was decided against the built bundle, not the source. One real bug
fell out: `.prep__done` asked for an undefined token, so its hard-coded fallback
always won — a light-palette green on a near-black panel at ~2.2:1.

**A farm can be provisioned, and can no longer be served from a developer dump.**
`tools/provision_owner.py` creates the first owner — the only account in the system
made without an authenticated actor behind it. It reads the password from the
terminal rather than an argument (a password in `argv` is in the shell history and
in `ps`), and refuses outright if an owner already exists: provisioning is first
boot, not a password reset, and the two want opposite behaviour.

> The guard that matters is `contexts/identity/reserved.py`. `DEVELOPMENT.md`
> publishes two account passwords, one an owner, and restoring a dump into another
> environment is routine — so the API now **refuses to start in production** while
> any account sits in a domain reserved for documentation (RFC 2606 / RFC 6761).
> Not a warning: a warning about credentials is read after the incident.
> `docs/RUNBOOK-FIRST-BOOT.md` is the procedure. `scripts/create_owner.py` was
> deleted — it was referenced by nothing and defaulted to the published password.

**Backup and the restore drill have now actually been run.** Both were correct and
had never once been executed. `backup.sh` produced a verified dump and a base
backup; the dump restored into a scratch database at schema head. Two real defects
fell out and are fixed:

> The drill demanded rows in `payment_notifications`, so it **failed on any farm
> that had not yet taken a payment** — which is every farm in its first week,
> exactly when the first drills run. It now compares restored counts against the
> live database, which keeps the failure it was built to catch (a backup pointed
> at the wrong database, producing a valid empty dump nightly) and drops the false
> alarm. And `backup.sh` wrote the dump under its final name while still filling
> it, so a drill overlapping a slow backup would pick a half-written file; the dump
> is now renamed into place only after it verifies.

**`deploy/systemd/` holds the units that run the farm** — the stack, a nightly
backup, a weekly drill — verified with `systemd-analyze`. This is the piece of
INFRASTRUCTURE Stage 2 that closes a measured risk rather than a theoretical one:
`pg_archivecleanup` runs only inside `backup.sh`, so with nothing scheduling it,
archived WAL grows without bound. The dev stack reached **847 segments / 13.9 GB in
four days**, and `compose.prod.yml` already carries a comment recording the earlier
version of this failure at 23 GB.

**There is a farm, and it survives being mistreated.** `192.168.29.148`, Ubuntu
26.04 under VMware, root grown to 96 GB and `/mnt/backup` on a **second physical
disk** — the ADR-0019 separation the compose default violates. Console on
`:8080`; the API is reachable only through its Caddy at `/api`, and postgres and
redis are not published at all. The storefront is **not** here and will not be:
ADR-0016 puts it on the edge VPS, and `web-dist` is a bundle rather than a server.

Measured on that host, not asserted: a reboot brings back the mount, the stack,
the timers and the data unattended; `SIGKILL` to the API is healthy again in ten
seconds; `SIGKILL` to postgres keeps the data through WAL replay and the API
recovers its pool without restarting. Backup and drill both run green through
systemd.

> **Do not trust `systemctl is-active printorian.service`.** `Type=oneshot` with
> `RemainAfterExit=yes` means "ExecStart returned 0 once", not "the farm is up".
> Observed within the hour: `systemd says: active | containers running: 0`, and
> `systemctl start` will not fix it — only `restart` re-runs ExecStart. The honest
> checks are `/health/ready` and the container count.
> `printorian-ensure.timer` reconciles every five minutes and logs loudly when it
> has to act; it reconciles toward systemd's *intent*, so a farm deliberately
> stopped stays stopped.

**Nine defects that only a real host could surface.** Four of them were in units
committed hours earlier: the image copied `tools/` but not `scripts/`; nothing
mounted `backup.sh` into postgres; the api container had no `/backup`; the api
image had no `pg_restore`. Three more were worse:

> **A write that was never committed.** `Database.session()` commits *after* the
> yield, so `return` from inside `async for session in db.session()` leaves the
> generator suspended and the commit never runs — the interpreter finalizes it
> with `GeneratorExit`, a `BaseException`, which slips past the `except Exception`
> that would at least have rolled back loudly. `provision_owner.py` created the
> farm's first owner, discarded the insert, printed "Created owner" and exited 0.
> `api/ws.py` had the same shape. `tests/unit/test_session_lifecycle.py` now walks
> the AST and fails on the pattern anywhere.
>
> **The drill could never have run on a farm.** Synchronous SQLAlchemy resolved a
> bare `postgresql://` URL to psycopg2 — declared only in the *dev* group, for one
> migration test. Exactly the failure INFRASTRUCTURE §6 predicts. Now async.
>
> **One full backup disk wedged WAL archiving permanently.** `archive_command`
> copied to the final name, so a full disk left a 786 KB fragment of a 16 MB
> segment there; `test ! -f` then saw a file and the `&&` chain never ran again —
> and freeing the disk did *not* help. It now writes `%f.tmp` and renames.

**The farm now says when its backup guarantee stops holding.** With the disk full,
`/health/ready` used to answer `{"status":"ok"}` while archiving failed and
`pg_wal` grew toward filling the *data* disk; `systemctl --failed` listed nothing.
It now reports `wal_archiving: degraded` — degraded rather than failed, because
serving is unaffected and taking the API out of rotation would turn a broken
backup into a broken farm. It compares the two watermarks rather than
`failed_count`, which never resets and would leave a farm red for ever over one
bad night in March.

**Archived WAL is gzipped, which changes the disk arithmetic.** `archive_timeout`
is 1 min, so segments are switched on *time* rather than fullness — 288 segments a
day on an idle farm, nearly all empty, at 16 MB each. Measured: 80 MiB of segments
compress to 2.95 MiB (**27×**), and the near-empty ones go 16 MiB → 16 KiB. That is
a 98 GiB backup disk filling in **22 days** versus over a year. PITR therefore needs
`restore_command = gunzip -c /backup/wal/%f.gz > %p`; a `cp`-based one finds nothing
and PostgreSQL reports recovery *complete* rather than failing.

**The console was rendering without its fonts.** Caddy's CSP said `font-src 'self'`
while Vite inlines the interface font as a `data:` URI, so every Harvester face was
blocked and the console fell back. Invisible in development, where the dev server
sends no CSP at all.

## 2. Deliberately unfinished

Not oversights. Changing any of them is a decision, not a cleanup.

| | Why it is like that |
|---|---|
| `customer_storage_quota_bytes` displayed, not enforced | Refusing a quote mid-configuration is the wrong UX. Growth is bounded by `model_retention_days` instead. |
| Rate limiting and sign-in lockout are in-process | Correct for one API process (ADR-0003). Counters reset on restart; a second replica would get its own allowance. `docs/DATABASE-REVIEW.md` §9. |
| No `/metrics` endpoint | Stage 5. `/health/workers` gives the honest liveness signal meanwhile — it reads beats each worker loop records at the *end* of a pass, so it distinguishes wedged from working. |
| Off-site backup sync has a recipe, no committed job | Needs farm-specific credentials. |
| Storefront `body` lifts the page ground | Predates Harvester; `--hv-bg` vs `--hv-void` is six values out of 255 in dark, identical in light. A visual call, not a cleanup. See `apps/web/src/app.css`. |
| TypeScript held at 5.x | `openapi-typescript` crashes on TS 7. Reason and three failed workarounds are in `.github/dependabot.yml`. |
| ~12 queries still sort on a timestamp alone | See below — a latent flake class, fixed only where it has actually bitten. |

**The single-column time sort is a flake waiting to happen.** `SettingsService.history`
ordered by `changed_at DESC` and nothing else. Under `FixedClock` every row in a test
shares one timestamp, so the sort ties and the planner may return either order — CI and
a dev machine disagreed about the same two rows. It now orders by `id` as well, which
settles it *correctly* rather than merely consistently: `core.ids.new_id` builds a
UUIDv7 from `time.time_ns()`, the real clock, so ids stay chronological where
`changed_at` is frozen.

The same shape is still in about a dozen queries — `grep "order_by(" printorian/` and
look for one term. Most cannot be observed by a test today. Two are worth knowing about:
`production/planning.py` (the scheduler: equal priority *and* equal `created_at` picks
arbitrarily) and `production/reads.py` (assignment records, whose whole purpose is
explaining the order things were considered in). They were left alone because fixing a
sort nothing asserts on is churn — but when one of them goes flaky, this is the cause,
and the fix is a second `order_by` term, not a retry.

## 3. What is actually next

Verified against the code, not read off a plan document.

**Five design-kit screens have no implementation at all** — all control-realm:

| Screen | Backend state |
|---|---|
| `settings.html` | **Rates half landed** (§1): `contexts/settings` serves the 17 scalar rates with an audit. The remaining ~85 kit parameters are still `core.config.Settings` constants read once at process start, so moving them changes *when* they are read as well as where from. No screen yet either way. |
| `purchasing.html` | Nothing. No `PurchaseOrder`, no `Supplier`. |
| `service.html` | Backend half exists (`ServiceOperation` is a real service card); no screen, no route. |
| `store.html` (warehouse) | Backend half exists (`MaterialLot` carries locations); no screen. |
| `logistics.html` | Only `carrier_code` on a parcel. No shipments, no tracking. |

Every public screen is built. Detail, and what each of the five would need from
the backend, is in [docs/DESIGN-KIT.md](docs/DESIGN-KIT.md).

**Still persisted or served with no consumer:** `GET /materials/{code}` (the
console reads the table and `/materials/lots`, never the per-code detail),
`EstimateVariance`, and `RateSnapshotRecord`.

**Still persisted with no endpoint:** `EstimateVariance` (drives `price_review`
and the desk's «Пересмотр цены» filter) and `RateSnapshotRecord`. `TelemetrySample`
was on that list and no longer is.

## 4. The first real print — started, and blocked on hardware

`printorian.drivers.bambu` has still never talked to a printer. Phase 4's exit
criterion was demonstrated with the `mock` driver, and `tools/bambu_spike.py`
proved the *protocol* in standalone code importing nothing from Printorian. The
product's own path between the two is the largest unproven assumption in the
system, and the one that could still say the design is wrong.

What exists now so that proving it is one command rather than a project:

- **`tests/contract/test_bambu_hardware.py`** — the same contract the mock driver
  is held to, run against a real machine. Credentials come from the git-ignored
  `printers.local.toml`; without it every test **skips**, so CI is untouched and
  nobody needs a printer to work on the rest of the system. Read-only by default —
  connect, capabilities, telemetry, and that wrong credentials are *refused* rather
  than answered plausibly. The half that physically prints is behind a second
  opt-in, because a suite that can start a print by accident is one nobody runs.
- **[docs/RUNBOOK-FIRST-PRINT.md](docs/RUNBOOK-FIRST-PRINT.md)** — the procedure,
  in order, with what each failure *means*. Step 1 separates "the network or the
  credentials" from "our code", which are indistinguishable from a distance and
  have completely different fixes.

**This needs you and a printer.** Nothing further can be verified without one.

## 5. Needs a person, not an agent

- **Dev account passwords have drifted from the docs.** `DEVELOPMENT.md` lists
  `floor@printorian.example` / `shop-floor-pass-1`; the stored hash does not match.
  `boss@printorian.example` was reset to the documented `owner-pass-12345` and
  works. Also: `floor@` is `engineer` in the database, `operator` in the docs —
  a role is an authorization decision, so it was left alone.
- **Stage 2 is now half done by hand, and that is the argument for Ansible.**
  A farm host exists and works (§1), but every step of getting there was manual and
  is recorded nowhere executable. The exit criterion is "a wiped machine reaches a
  running production farm from `ansible-playbook` plus one SOPS key", and the
  cheapest time to write that role is now, against a host whose correct end state
  is known and reproducible. Ansible cannot run on a Windows control node, so it
  needs WSL or a container.
- **Stage 3 still needs hardware nobody has.** "The storefront serves over HTTPS on
  the real domain" needs a VPS, DNS and an object-storage bucket. Until then there
  is no customer-facing site anywhere — only the console, on the LAN.
- **Off-site backup is still the largest single gap.** Every copy of the farm's
  data is on one machine. §1's compression buys time on the local disk; it does
  nothing about fire, theft or that VM being deleted.
- **The storefront ground colour** (§2) — keep the lift or take Harvester's void.
- **`ruff format --check` was failing on `main`** before this run, on a migration
  committed as raw alembic output. Fixed in passing; worth knowing the gate can
  drift without anyone noticing, because a red CI on `main` is easy to live with.

## 6. If you are picking up mid-flight

Two things this repository will not tell you and that cost an afternoon each:

1. **Check `git status` first.** Agent work has been interrupted mid-write more
   than once, leaving a tree that compiles, passes lint, and has no tests for the
   part that matters. All six gates passed on a change whose only irreversible
   path was untested.
2. **Do not trust an agent's report of its own verification.** Reproduce it. Two
   separate reports of "all gates green" were produced by runs where the gate had
   not been executed at all.
