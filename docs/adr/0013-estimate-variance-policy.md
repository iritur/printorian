# ADR-0013 — Quoted price is binding within a tolerance band

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
ADR-0006 puts slicing after checkout. So the customer's price comes from a mesh heuristic,
while the true print time and filament mass arrive later, when an engineer slices. Without
an explicit rule this becomes silent margin leakage that nobody notices for a year.

## Decision
`EstimateSource` is tracked explicitly: `MeshHeuristic` -> `PreparedPlate` -> `Measured`.

When a prepared plate's cost exceeds the quoted cost by more than
`price_variance_tolerance` (config, default 15%), the job does **not** dispatch - it routes
to `PriceReview` with the delta shown. Within tolerance the quote is binding and the farm
absorbs the difference.

Every variance is recorded.

## Consequences
* Recorded variances calibrate the mesh estimator against real slicer output; the estimator
  is expected to be wrong at first and to improve measurably (Phase 6 calibration report).
* `PriceReview` is a real state in the order machine, with an owner and an SLA.
* The tolerance is configuration, not a constant buried in code.
