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
