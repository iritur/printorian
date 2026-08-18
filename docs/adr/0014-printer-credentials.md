# ADR-0014 — Printer credentials are encrypted at rest, never in git

**Status:** Accepted · Phase 1 · 2026-08-05

## Context
Every Bambu printer on the LAN is reached with a **serial** plus an 8-digit **access
code**. The access code is a credential: anyone holding it and the IP can read the
machine's state, upload files to it, and start prints.

A farm has one printer during the spike and is expected to have many. Retyping a
serial and an access code per invocation does not scale, and the obvious shortcuts —
a committed config file, a shared `.env`, a constant in a script — all end with
credentials in version control, where they are effectively permanent.

## Decision

**Now (spike tooling).** `backend/printers.local.toml` holds host, serial and access
code per printer, keyed by a short name. The file is git-ignored;
`printers.local.toml.example` is committed so the format is discoverable. The spike
resolves `--printer <name>`, and `--host` / `--serial` / `--code` still override it.
`bambu_spike.py printers` lists the registry and **never prints access codes**.

**From Phase 3 (the product).** The fleet context owns printers as database records.
The access code is stored **encrypted at rest**, with the key supplied through
`PRINTORIAN_SECRET_KEY` and never written to the database. The plaintext code exists
only in memory, inside the driver, for the duration of a connection.

Consequences of that shape, to be honoured when Phase 3 lands:

* The access code is **write-only over the API**. It can be set and replaced; no
  endpoint ever returns it, including to an owner. A UI shows "set" or "not set".
* Reading or changing it requires `Permission.MANAGE_FLEET`, enforced in the API
  layer like every other permission (ARCHITECTURE §10).
* A database dump is not enough to take over the farm's printers — the key lives
  outside it.
* Rotating a code is a first-class operation: the printer regenerates it when LAN
  Mode is toggled, so the system must expect codes to change during a machine's life.
* Logs and error details never include the code. `IntegrationError` details carry the
  printer id, never its credentials.

## Alternatives rejected

* **Environment variables per printer** (`PRINTORIAN_PRINTER_07_CODE`) — does not
  scale past a handful, and makes adding a machine a deployment rather than a
  data entry.
* **Plaintext column in the database** — simpler, but then a routine backup copied to
  a laptop carries live credentials for the whole farm.
* **External secret manager** (Vault and similar) — correct at larger scale, but a
  second piece of infrastructure to run and back up for a single on-prem server
  (ADR-0003). Revisit if the farm becomes multi-site.

## Note on this project's history

The bench printer's access code was pasted into a working transcript before this ADR
existed. It is a LAN-only credential on a private network, so the exposure is small,
but the general rule stands: a credential that has been shared should be rotated.
Toggling LAN Mode off and on regenerates it, and the registry file is the only place
that needs updating afterwards.
