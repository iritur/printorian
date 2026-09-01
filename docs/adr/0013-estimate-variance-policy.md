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

**Why a difference and not a fresh total.** The **per-line quote does not exist**
— `OrderingService.place` prices the order and then apportions the total across
lines by quantity, so there is no stored number a fresh total could be compared
against. That alone settles it. The **customer tier** is the second input that no
column on the order holds; it is resolved from spend at checkout and only its
*effect* survives, inside `price_breakdown`. A difference cancels both, along with
the shipping choice, procurement, the finishes and the AMS purge, and leaves only
what actually changed.

> **Correction (round two of #92's review).** This paragraph used to say that
> rebuilding the tier from `price_breakdown` "is a second implementation of the
> loyalty ladder". That is not true and should not be relied on:
> `engine._adjustment_lines` writes the *applied* `tier.discount_percent` onto
> the ADJUSTMENT_CUSTOMER_DISCOUNT line's `Basis.percent`, `_margin_line` writes the
> effective margin — override included — onto the MARGIN line's, and
> `breakdown_from_dict` reads both back. Reading two numbers off a stored
> breakdown is not the ladder. The argument for the difference is the per-line
> quote above, which really is unrecoverable.

**Where the residual error is not conservative.** Cancelling the tier makes the
delta *larger* than the truth for every tier that discounts, and ADR-0013's band
is one-sided, so that error can only hold a job for a person. The exception is a
tier whose `margin_percent_override` is **above** the snapshot's `margin_percent`:
the change is then marked up less than the customer's own book would, the overrun
is understated, and one that should have gone to `PRICE_REVIEW` can land inside
the band. On the farm's defaults — 45% against 30%, on a plate a quarter longer
and heavier than the mesh guessed — the true overrun is 15.48% and the recorded
one is 13.88%. `tests/unit/test_reprice_tier.py` pins both the sign and that
straddle, so the gap is a documented cost rather than a surprise.

**Where it refuses.** No plate, more than one plate for the configuration, a
plate in another material or at another scale, a plate an engineer has retired, no
pinned snapshot, a stored payload that will not rebuild at all, a snapshot that
rebuilds but no longer hashes to its own content, an order priced by an engine
version this release is not, a material no longer in the catalogue, a plate with
no minutes, a multi-line order, a plate whose recorded layout does not match what
was ordered, a plate that does not record its layout at all, a plate whose
filament count is not the line's, a plate holding more than one copy, a line whose
part was never measured, or a plate that is numbers with no file behind it: each of
those leaves the job `PENDING` and the order in `PREP`. None of them guesses. That
list is the shape of this ADR's obligation — a variance nobody measured is worse
than no variance, because it *looks* measured.

> **The list itself is now a module, and that is the point of the fourth review.**
> Three reviews of [#92](https://github.com/iritur/printorian/pull/92) each found
> exactly one unguarded dimension, each because somebody went looking for that
> particular one, because nothing named the set. `plate_key` answers "have we
> sliced this before" and every term in it describes the *order*; nothing in the
> schema describes the *bed*. `backend/printorian/workers/plate_admission.py` is
> the enumeration — what must match, what is checked elsewhere, what is
> deliberately absent (finishes cancel on both sides of the difference), and what
> is still open with the cost of closing each. Add a refusal there, with its own
> code and its own test, rather than a fourth branch in `_usable_plate`.

**The layout refusal is the one worth explaining**, because its absence is
invisible and because this ADR previously described it wrongly. A `PrintJob` is
one plate holding a whole line's work, so how many copies fit on the bed is the
engineer's decision at prep — and two things depend on it: `attach_plate` writes
the plate's minutes and grams onto the job as its total work, and the reprice
divides those same totals by the line's quantity.

Get the count wrong in either direction and both go wrong quietly. A one-up plate
on a line of three prints a third of what was sold and reprices at a third of the
work — inside the band, flattering. A **multi-up plate on a line of one** does the
mirror image, and that is the *normal* cache entry, because the first order for
two is what leaves a two-up plate behind: measured on a 20 min / 8 g plate against
a 10 min / 4 g quote, the overrun comes out at 4.26%, well inside the band. The
farm prints two, ships one, and records an accurate estimate.

The first attempt at this guard refused only the first direction — any line whose
quantity was not one — which left the second wide open. `PreparedPlate.copies`
(migration `0023`) is the fix: nullable, never defaulted, never backfilled, because
a `1` written in for the plates already in the table would be an invented number
that happens to be exactly the one that makes the common case attach. The
unattended path attaches only when the plate's recorded `copies` equals the line's
quantity, and never when the plate does not say.

> **And a multi-up plate is refused anyway, for a second reason found later.**
> Recording the count made a line of three attachable to a three-up plate; what is
> recorded nowhere is the bed's own **footprint**. The only geometry the planner
> ever sees is the job's — one part's box — so `fleet.can_take`'s single geometric
> test judges a three-up bed by the size of one part, and a machine that cannot
> hold the plate is eligible for it. Scaling the job's box by the copy count would
> be an invented number in the other direction (a 2×2 layout of four parts is twice
> the width, not four times), so the unattended path takes the cache miss. The
> cost is stated where the guard is: recording the plate's bed extent — two
> columns and a console field, exactly the shape `copies` took — turns the check
> into "the recorded footprint fits the machine" and lifts the refusal.

**`PRICE_REVIEW` is now reachable straight from `PAID`.** The band can be exceeded
before any engineer has touched the order, so the order machine says so rather
than recording a `PREP` the order never entered.

**One input is live rather than pinned, and it is not in the snapshot.**
`MaterialSpec.sell_price_per_gram` is a mutable catalogue column, and
`workers/cached_plates.py` reads today's value for *both* sides of the difference.
The residual is therefore `(plate_grams − quoted_grams) × (price_today −
price_when_quoted)` — bounded by the mass difference rather than by the total, and
zero whenever the filament has not moved — but it is a live number entering a
figure ADR-0020's amendment otherwise describes as computed under the order's own
pinned rates. Removing it means carrying the line's price per gram onto `OrderLine`
at `place()` time, which is a change on the checkout path and has not been made.
