# ADR-0001 — One backend, one database, one domain model

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
A storefront and a farm console are tempting to build as two systems: each owns its own
store, and a sync service reconciles them. The pull is real, because each side's data
looks self-contained until you need a join across it.

The scenario's requirement "all data should be interconnected" is not a feature. It is an
architectural constraint, and mirroring the domain violates it by construction — the two
copies diverge silently, and no build step can detect that they have.

## Decision
One PostgreSQL database, one SQLAlchemy domain model, owned by one backend. The web SPA
and the Electron desktop are both **clients**. There is no sync layer because there is
nothing to sync.

## Consequences
* Every new field is added once, not in two entity models plus a mapper.
* The console requires the backend to be reachable. Accepted: it sits on the same LAN as
  the printers it controls.
* Offline console operation is out of scope.

## What mirroring costs, concretely
Under a mirrored design, adding one material property means editing the owning entity, its
mirrored twin, the transfer object between them, both schemas, and the mapper — five places
for one field. The mapper is the dangerous one: it silently drops anything it was not
taught about, so the failure is a missing value rather than an error.
