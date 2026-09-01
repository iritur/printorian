# ADR-0020 — Rate snapshots are persisted, not merely hashed

**Status:** Accepted · 2026-08-11 · Reinforces ADR-0002

## Context
ADR-0002 and ARCHITECTURE §5 both claim that an order's price "can be recomputed
years later". The order stored `rate_snapshot_id` — a content hash of the rate
bundle — alongside the pinned `Breakdown` and the engine version.

The hash was not enough on its own. It proves *which* rates were used and detects
tampering, but the values behind it lived only in `pricing/rates.py` defaults and in
whatever the API edge assembled at the time. Change a rate and every older hash
became unresolvable: the stored breakdown could still be *displayed*, but the quote
could not be *recomputed*, and the difference is the whole of the claim.

The gap had not hurt because no rate had changed. Slice D — the settings store,
whose entire purpose is letting an owner change `margin_percent` — would have made
it hurt on the first save.

## Decision
A `rate_snapshots` table: primary key is the content hash, payload is the whole
`RateSnapshot` as JSONB, written insert-only on first use. `orders.rate_snapshot_id`
is a real foreign key with `ON DELETE RESTRICT`.

`pricing.serialization` gains `rates_to_dict` / `rates_from_dict`, written over
`dataclasses.fields` rather than a hand-listed set of keys — a hand-listed
serializer silently omits the next rate somebody adds, and a snapshot missing a rate
is worse than no snapshot at all, because it looks complete and reproduces a
different number.

**Pricing purity is untouched.** The engine still receives rates and never fetches
them; the table is written by `ordering`, which is the context that pins the
reference. `pricing` may not import SQLAlchemy and does not.

## Consequences
* `OrderingService.place` takes the `RateSnapshot` alongside the `Breakdown`, and
  writes it with `ON CONFLICT DO NOTHING` — the id is the content hash, so two
  orders priced from identical rates race to insert the same row and the loser has
  nothing to correct.
* `RESTRICT` rather than `CASCADE` or `SET NULL`: a cleanup job must not be able to
  strand an order whose price depends on the row.
* Slice D's settings store gets its audit trail for free — a settings change
  produces a new snapshot row, which is exactly what `settings.html` shows.
* Round-tripping is asserted by the snapshot id, so a rate whose *type* the
  serializer cannot carry fails the test suite rather than silently degrading a
  stored quote.

## Amendment — the first thing that reprices from a snapshot (2026-08-31)

Until now the persisted payload had exactly one consumer, and it was a *read*:
`GET /orders/{id}/rates` serves the row verbatim so a person can look at it.
`rates_from_dict` existed and nothing in the product called it.

[#58](https://github.com/iritur/printorian/issues/58) is the first path that
actually **runs the engine against a stored snapshot**: the intake sweep reprices
a paid line from a cached plate's minutes and grams to get ADR-0013's
`prepared_cost`, and it must do so at the rates the order was sold under, not at
today's. That is the guarantee this ADR was written for, finally being spent.

It comes with one guard, and it belongs here because the trap is this document's.
`ordering/snapshots.py` is explicit that `rates_from_dict` is the wrong tool for
*serving* a row: it skips fields the row does not carry, and `RateSnapshot` then
supplies today's default for them, so an old snapshot comes back holding a number
that was never in force. Repricing has no alternative tool — the engine takes a
`RateSnapshot`, not a dict — so the rebuilt object is checked against its own key:

> **The id is the content hash of the values. A snapshot rebuilt from a stored row
> whose hash does not match that row was completed from today's defaults, and is
> refused.**

The order then goes to prep and an engineer prices it. Without that check, adding
one rate to `RateSnapshot` would silently re-rate every older order this path
touches, and nothing in the result would say so — which would undo this ADR by the
exact mechanism it was written to prevent, one release later.

**The rates are half of a reproducible price; the engine is the other half, and
that half is now checked too.** ADR-0002 is explicit that pinned rates alone do not
fix a result — the calculation shape has to be pinned with them, which is what
`Order.engine_version` and `RateSnapshotRecord.engine_version` are for, and
`pricing.delta` already computes a `comparable` flag on exactly this mismatch.
`prepared_cost` is `line_total`, produced by the engine of the day, plus a
difference computed by today's; let `ENGINE_VERSION` move with a changed labour or
margin rule and that sum is a hybrid nobody priced, entered on ADR-0013's table as
measured, with no column on `EstimateVariance` to say so. `_rates_for` refuses a
moved engine on either record, exactly as it refuses a drifted snapshot. It was
raised as a bounded residual in the third review of #92 and closed rather than
written down, because refusing costs one engineer's click.

**And one input of that reprice is not pinned, because it was never in the
snapshot.** `RateSnapshot` carries no material price;
`MaterialSpec.sell_price_per_gram` is a mutable catalogue column, and
`workers/cached_plates.py` reads today's value for both sides of the difference it
computes. Since ADR-0013's `prepared_cost` is a difference, what survives is
`(plate_grams − quoted_grams) × (price_today − price_when_quoted)`: bounded by the
mass difference rather than by the total, and zero whenever the filament has not
moved. It is written down here because the sentence above — "at the rates the order
was sold under, not at today's" — is otherwise read as covering every input, and it
does not cover this one. Closing it means carrying the line's price per gram onto
`OrderLine` at `place()` time, a change on the checkout path rather than this one.
