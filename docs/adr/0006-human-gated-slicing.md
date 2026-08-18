# ADR-0006 — Slicing is human-gated, and its output is cached

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
Turning a customer's STL into a printable plate requires slicing. Fully automatic slicing
needs a headless slicer CLI and a per-printer, per-material profile matrix, plus a failure
path for un-sliceable geometry. The farm chose a human-in-the-loop step instead.

Taken naively, that means engineer labour scales linearly with order volume and the farm
stops being a farm.

## Decision
A paid order routes to an engineer's **prep queue**. The sliced output becomes a
first-class cached entity, `PreparedPlate`, keyed by
`(model_asset, scale, material_spec, printer_profile, plate_layout_hash)`, carrying exact
print minutes and per-slot filament grams plus provenance (who sliced it, slicer and
profile version).

## Consequences
* First order of a configuration is manual. **Every repeat order of it dispatches fully
  automatically.**
* Plates invalidate when the model or the profile changes - hence the layout hash and
  version fields.
* Prep-queue depth is a monitored metric from Phase 4. If it saturates, that is the trigger
  to reopen headless slicing.
* Price is quoted from a mesh estimate before the true numbers exist - see ADR-0013.

## Amendment — the round trip is manual (Phase 4, 2026-08-10)

[ADR-0016](0016-two-web-apps-no-desktop.md) removed the desktop app, and with it
the only thing that could launch a slicer or watch a folder. The gate stays human;
the mechanism changes.

The console **offers the model for download**; the engineer slices it in their own
Bambu Studio or Orca; the console **takes the plate back as an upload**. The server
parses print minutes and per-slot grams out of it, exactly as the desktop app did.

Two manual steps replace one. That is a smaller cost than it sounds, because the
`PreparedPlate` cache means it happens **once per configuration, not once per
order** — which was already this ADR's central claim. It also happens to be the
only thing that works today: without model storage there is nothing to hand a
slicer by any mechanism.

The escalation path is unchanged, and now has two rungs rather than one:

1. a headless farm agent that launches and watches, if the manual step chafes;
2. server-side headless slicing, if prep-queue depth saturates.

Neither is built. Both remain reopenable with an ADR, on evidence from the
measured queue depth rather than on anticipation.
