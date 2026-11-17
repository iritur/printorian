---
name: printorian-decisions
description: Printorian's settled questions — the ADR-locked decisions D1–D13 in one line each, the ruled-out list with reopening conditions (SQLite fallback, runtime plugins, Electron, floats in pricing, hand-edited API client, TS 7 hold...), and the 2026-11-17 owner rulings on deployment shape (containers under systemd, no microservices, no host installs) and the printorian-server host pivot. Use before proposing anything architectural, infra-shaped, or "simpler"; prevents re-litigating decided questions.
---

# Decided questions — do not re-litigate

The full-fidelity records are the ADRs in `docs/adr/` (locked; changing one
requires a superseding ADR, not a commit) and, for recent rulings, the
memory records in `.zcode/memories/`. This file is the index you check
*before* proposing something.

## 1. Locked by ADR (one line each)

| | Decision |
|---|---|
| 0001 | One backend, one database, one domain model — no mirrored entities, no sync layer. |
| 0002 | The pricing engine is a pure function — no I/O, no clock, no floats; rates are given, never looked up. |
| 0003 | The backend runs on-premises on the farm LAN; the internet host holds no data at rest. |
| 0004 | (superseded by 0016) Electron console — kept for its reasoning. |
| 0005 | Both clients consume a generated TypeScript API client — never hand-edited, regenerated in CI. |
| 0006 | Slicing is human-gated and its output is cached (`PreparedPlate`) — first order manual, repeats automatic. |
| 0007 | Drivers never simulate silently — `DriverUnavailable` → `Offline` + attention; governs the whole repo (null ≠ 0). |
| 0008 | Alembic is the only schema mechanism — one head; untested downgrade is not a rollback plan. |
| 0009 | No runtime plugin loading — only drivers/payment providers/shipping carriers via entry points. |
| 0010 | Single-tenant now, tenant-safe seams — no hardcoded rates; `Settings` is the only home for tunables. |
| 0011 | The driver interface is brand-neutral — drivers depend on `core` only. |
| 0012 | The backend emits machine-readable codes, never localized prose. |
| 0013 | The quoted price is binding within a tolerance band (default 15% → `PriceReview`). |
| 0014 | Printer credentials are encrypted at rest, never in git; agents never write them. |
| 0015 | Live events invalidate the client's view; they do not carry it. |
| 0016 | Two web apps, no desktop app — storefront (internet) + console (LAN). |
| 0017 | JSONB is the JSON storage type (`core.db.JsonB`). |
| 0018 | High-volume tables are time-partitioned with explicit retention. |
| 0019 | Backup is WAL-archived, off-site, and drilled — an untested backup is not a backup. |
| 0020 | Rate snapshots are persisted and pinned per order — changing a rate never reprices quoted work. |
| 0021 | Tests run on real PostgreSQL, no SQLite fallback. |

## 2. Ruled out — with the condition for reopening

- **SQLite test fallback** (ADR-0021) — reopening condition: none foreseen;
  it deleted coverage for three features last time.
- **Runtime plugin system** (ADR-0009) — reopen only with a concrete driver
  or provider that entry points cannot express.
- **Electron/desktop console** (ADR-0016 supersedes 0004) — reopen only if
  the LAN console's browser surface proves insufficient on the shop floor.
- **Floats anywhere in pricing** (ADR-0002) — never.
- **Hand-editing the generated API client** (ADR-0005) — never; the hook
  enforces it.
- **Adding the postprocess operations catalogue to `RateSnapshot`**
  (ADR-0020 amendment) — it would change every stored snapshot hash; reopen
  only with a migration story for the whole `rate_snapshots` table.
- **TypeScript 7** — held until `openapi-typescript` accepts it
  (`npm view openapi-typescript peerDependencies`); three workarounds were
  tried and all fail, listed in `.github/dependabot.yml`.
- **Prettier as a gate / mass reformatting** — not a CI gate; reformatting
  files you are not editing makes the diff stop being about the change.
- **Renaming glossary terms in code** — the English term is the identifier
  (docs/GLOSSARY.md); the "deliberately not used" list (`WorkOrder`,
  `Spool`, `PrintJob`, `Quote`) is a decision, not a TODO.
- **Removing the retention clamp watermark** in `workers/maintenance.py` —
  `min(now − retention, watermark)` is the only thing between a stalled
  summariser and irreversibly deleted history; see backend/CLAUDE.md
  "Do not fix".
- **Enforcing `customer_storage_quota_bytes`** — displayed, not enforced,
  on purpose (refusing mid-configuration is the wrong behavior).
- **The storefront `body` ground-rule lift** (`apps/web/src/app.css`) — a
  visual decision, not a cleanup.

## 3. Deployment and host shape (owner rulings, 2026-11-17)

- **The app ships as containers under systemd compose.** Modular monolith,
  one image per app, digest-pinned, cosign-signed, pull-based delivery.
  **No microservices** — boundaries are bought statically (import-linter);
  "the release unit of this system is the commit, not the service"
  (INFRASTRUCTURE §2, §11 non-goals).
- **No host-level installs of the application.** The CI release gate —
  migrate round-trip, readiness, virtual-farm E2E, Trivy, SBOM, cosign — is
  the trust boundary; `pip install` on a host runs code CI never proved.
- **The host layer is `printorian-server`** — a custom minimal hardened
  Arch system (kernel, pacman repo, ISO installer, backup service,
  monitoring), in its own repo mirrored to gitverse; charter recorded in
  `.zcode/memories/printorian-server-charter.md`. This supersedes the
  Debian farm-host choice in INFRASTRUCTURE §3 (containers-under-systemd
  and pull-based delivery unchanged). INFRASTRUCTURE.md still describes
  Debian — a known doc-drift, to be fixed by a docs issue, not by quietly
  editing around it.

Reopening anything in this section takes an owner decision or a measurement
the current ruling did not have — bring evidence, not preference.

## 4. Gotchas with recorded incidents

The traps behind these rules (measured numbers, failure stories) live in
`.zcode/memories/` — grep `MEMORY.md` for the module you are touching
before proposing around a rule; the incident that set it usually has a
number attached.
