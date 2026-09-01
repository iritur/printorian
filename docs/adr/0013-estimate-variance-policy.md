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

## Amendment — where `prepared_cost` comes from (Phase 4, 2026-08-31)

This ADR asks for "a prepared plate's cost" and, until now, nothing in the system
produced one. Pricing happens once, at quote time, from the mesh heuristic; a
`PreparedPlate` carries minutes and grams and no money at all. So every variance
the farm has ever recorded had its `prepared_cost` supplied by whoever was
attaching the plate, and the automatic intake path
([#58](https://github.com/iritur/printorian/issues/58)) had nobody to supply it —
which is why [#41](https://github.com/iritur/printorian/issues/41) stopped short of
attaching cached plates rather than passing a zero.

**A prepared cost is now derived, and it is derived as a difference:**

```
prepared_cost = line_total + ( price(spec with the plate's minutes and grams)
                             - price(spec with the numbers that were quoted) )
```

both priced under the order's **own pinned `RateSnapshot`** (ADR-0020), by
`pricing.reprice.prepared_cost`.

**Why a difference and not a fresh total.** Two inputs of the original quote
cannot be recovered from the order, and each would land on the money column this
ADR exists to make trustworthy: the **customer tier**, whose loyalty discount is
resolved from spend at checkout and never stored; and the **per-line quote**,
which does not exist — `OrderingService.place` prices the order and apportions the
total across lines by quantity. A difference cancels everything the two prices
share, including both of those, and leaves only what actually changed. The
residual error is a percentage of the delta rather than of the total, and it is
in the direction that holds a job for a person.

**Where it refuses.** No plate, more than one plate for the configuration, no
pinned snapshot, a stored snapshot that no longer rebuilds to its own content
hash, a material no longer in the catalogue, a plate with no minutes, or a
multi-line order: each of those leaves the job `PENDING` and the order in `PREP`.
None of them guesses. That list is the shape of this ADR's obligation — a variance
nobody measured is worse than no variance, because it *looks* measured.

**`PRICE_REVIEW` is now reachable straight from `PAID`.** The band can be exceeded
before any engineer has touched the order, so the order machine says so rather
than recording a `PREP` the order never entered.
