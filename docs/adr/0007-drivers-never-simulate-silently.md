# ADR-0007 — Drivers never simulate silently

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
Hardware integration is the one part of this system that cannot be made reliable by
careful coding, so a driver needs an answer for "the printer did not respond". A fallback
that returns plausible synthetic state is the tempting answer: nothing crashes, screens
stay populated, development continues without hardware.

It is also the one answer that can hide a completely non-functional core indefinitely. A
system that fails into fiction reports plausible states and plausible job ids while
controlling nothing, and there is no symptom to notice — which is precisely why the rule
has to be structural rather than a matter of care.

## Decision
* A driver that cannot reach its printer raises `DriverUnavailableError`. The fleet context
  maps that to `Offline` and raises an attention event. There is no fallback data path in
  the driver interface, by design.
* The `mock` driver raises `ConfigurationError` at construction when
  `environment == production`.
* An unknown brand is a `ConfigurationError`, never a silent downgrade to a simulator.
* `manual` is a first-class driver for human-driven machines. It reports exactly what an
  operator declared and `None` for everything nobody measured - no invented progress,
  layers, or temperatures.

## Consequences
* A broken integration is loud and immediate.
* Every driver ships contract tests against recorded protocol fixtures; a driver without
  fixtures does not merge.
