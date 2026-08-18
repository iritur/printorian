# ADR-0011 — The driver interface is brand-neutral

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
An abstraction over one implementation is a guess. Building the driver layer against Bambu
alone would let Bambu's assumptions — its state names, its file format, its slot model —
leak into shared code, and nothing would reveal it until a second brand arrived.

## Decision
`printorian.drivers` depends on `printorian.core` only, never on a business context
(enforced by contract). Brands register by name; nothing else in the system knows a brand
exists.

The system drives **Bambu FDM + AMS**. Elegoo machines run on the `manual` driver - real,
schedulable printers whose state operators advance - until an SDCP driver lands in Phase 7.

## Consequences
* Adding a brand touches the registry and one new module. It must not touch `scheduling`
  or `production`.
* The Phase 7 Elegoo driver's real purpose is to prove the abstraction is not Bambu-shaped;
  Moonraker and PrusaLink are cheap further confirmations.
