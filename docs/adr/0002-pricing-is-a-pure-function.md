# ADR-0002 — The pricing engine is a pure function

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
Two things need a price: the quote shown to a customer, and the cost booked by the farm.
Implementing them separately is the obvious path and the fatal one — the copies drift on
exactly the details nobody re-reads (whether packaging scales with quantity, whether rush
applies before or after the failure buffer, whether price books exist at all), a docstring
claims one mirrors the other, and no test compares them because they are never called with
the same input.

The delta preview forces the issue independently: showing what an option *would* change
means pricing two hypothetical specs and subtracting. That is only honest if pricing is a
function.

## Decision
`price(spec: PriceSpec, rates: RateSnapshot) -> Breakdown` - a pure, deterministic,
versioned function. No database, no clock, no network, no configuration lookup, no floats.
Enforced by an `import-linter` contract: `printorian.contexts.pricing` may import
`printorian.core` primitives and nothing else.

## Consequences
* The scenario's per-option delta preview is `diff(price(a), price(b))` - no second
  implementation can exist to disagree with the first.
* Orders store `rate_snapshot_id` + `engine_version`, so any historical quote reprices
  identically.
* Rates must be passed in explicitly. This is deliberate friction.
* Pure modules import `printorian.core.money` directly, not the `printorian.core` facade,
  which pulls in the event bus.
