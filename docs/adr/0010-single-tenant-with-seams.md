# ADR-0010 — Single-tenant now, tenant-safe seams

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
The farm is the first and only customer. Multi-tenancy touches every context and would slow
Phases 1-5 substantially.

## Decision
Build single-tenant. Keep the seams that make tenancy addable: no hardcoded rates or
thresholds anywhere in a context (everything arrives via `Settings` or a `RateSnapshot`), no
module-level mutable farm state, no global singletons holding tenant-specific data.

## Consequences
* `printorian.core.config.Settings` is the only home for tunables.
* Adding tenancy later means adding a tenant key and a filter, not restructuring.
