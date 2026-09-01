"""What must match before a plate is printed with nobody looking.

**This module exists because three reviews of
[#92](https://github.com/iritur/printorian/pull/92) each found exactly one
unguarded dimension, and each was found only because somebody went looking for
that particular one.** Money on a job event, then a copy count, then a filament
set. A fourth patch to `_usable_plate` would have left the fifth to be found by
whoever was unlucky, so the enumeration lives here, once, as a list a reader
cannot miss and a function that fails closed on everything it does not know.

**The cause, stated plainly.** `plate_key` is
`(model_hash, scale, material_code, printer_profile, layout_hash)` and it was
built to answer *"have we sliced this configuration before?"*. That is a weaker
question than *"is it safe to send this to a machine with nobody watching?"*, and
every term in it describes the **order's** side: geometry, size, the priced
material, a profile string, an opaque layout. Nothing in the schema describes the
**bed** — what is physically on it, how big it is, how many filaments it calls
for, which machine it was cut for, whether there are any bytes to send.
`PreparedPlate.copies` (migration `0023`) was the first bed-side field ever added.
Every hole the reviews found was a bed-side property, and there was nowhere to put
one, which is why each was invisible until somebody looked for it by name.

---

## What must match, and where each is checked

**Enforced by `admits` below**, each with its own refusal code, and each failing
closed when the plate does not record the answer:

| Dimension | Refused when |
|---|---|
| Copies on the bed | not recorded, or not equal to `line.quantity` |
| The bed's own footprint | the plate holds more than one copy — nothing records the bed's box |
| Filament set | the plate's slot count is not the line's distinct colour count |
| The part's size at the ordered scale | the line's mesh was never measured |
| Bytes to send | the plate is numbers with no file behind them |

**Enforced before this function is reached**, and listed so the set is complete:

* **Geometry** — `model_hash`, a `plate_key` term and the `WHERE` of
  `PlateLibrary.find_unambiguous`; `CachedPlates._usable_plate` returns nothing at
  all for a line whose digest is empty.
* **Scale** and **material** — `plate_key` terms, enforced by `find_unambiguous`
  rebuilding each candidate's key from the row's own profile and layout.
* **Freshness** — `status == VALID` in `find_unambiguous`'s `WHERE`. Read the
  caveat below before relying on it.
* **One line per order** — `intake_routing._may_attach_automatically`. A
  multi-line order's `line_total` is a *share* of the order total, so the ADR-0013
  band would be applied to a number nobody was quoted.
* **The money** — `CachedPlates._rates_for`: the order's pinned `RateSnapshot`
  must rebuild to its own content hash (ADR-0020) and must have been read by
  today's `ENGINE_VERSION`.

**Deliberately not on the list.** Finishes. Every entry in `FINISH_CATALOGUE` is
post-production labour, a flat fee and extra days on the promise; none of them
changes what the slicer produced, so they are identical on both sides of the
reprice and cancel. Recorded here as a decision rather than left as an omission,
because the next reader would otherwise have to work it out again.

---

## What is still open, what it would cost to close, and why it is not closed here

None of these is guessed at: each is a thing the farm does not record, named so
the next reader finds it by reading rather than by luck.

* **Printer profile.** `PreparedPlate.printer_profile` says which machine the
  plate was cut for; nothing carries it to the planner — `JobRequirements` has no
  profile field and `Printer` has no profile column — so a plate sliced for an X1C
  can be uploaded to a P1S. `find_unambiguous`'s docstring used to be read as the
  protection against this; it only declines when there are **two** rows, and the
  console cannot produce two, because it sends no profile at all and `record`
  upserts on the key, so re-slicing for a second machine overwrites the first row.
  Closing it means a profile on `Printer`, a term in `JobRequirements` and a
  `reject.profile` in `fleet.can_take`.
* **Nozzle diameter.** Never recorded on a plate, and `PrintJob.nozzle_diameter_mm`
  is NULL on every job intake creates — which `can_take` reads as "any nozzle will
  do", a default that reads as data (CLAUDE.md §1) in the permissive direction. It
  is the one machine-capability test that correlates with the slicing profile, and
  it is switched off. Closing it means recording the nozzle the plate was sliced
  with, which arrives with the profile above.
* **Which colour is in which slot.** Only the *count* of filaments is recoverable
  from a plate (`len(filament_grams)`), and the count is what `admits` compares.
  Nothing records that slot 0 held white, so a two-filament plate cut for
  white+black still admits a white+red order. Closing it means a colour per slot
  on `PreparedPlate`, filled by the console.
* **Whether the bed carries anything else.** `copies` counts copies of *the*
  model. An engineer who puts a second, unrelated part on the same bed records it
  under one model's key, and `layout_hash` — the only field that could say — is
  the empty string on every row the product writes. The `copies != 1` refusal
  below narrows this without closing it.
* **Provenance of the plate's minutes and grams.** `POST /jobs/{id}/plate` accepts
  both as typed numbers, and nothing on the row says whether they were parsed from
  the file or entered by hand. They become the job's schedule and ADR-0013's
  *measured* `prepared_cost`. Requiring `content_sha256` below is not the same
  guarantee: that route accepts a digest in its body too.
* **The fleet layer is not a backstop, in either direction.** `JobRequirements`
  carries a catalogue spec code (`"pla-white"`) and colour *names* (`"white"`),
  while `AmsSlot` carries the machine's tray type (`"PLA"`) and a colour *hex*
  (`"#F5F5F5"`). Measured with a probe against the real `can_take`: a job for
  `"white"` is rejected by a printer loaded with white, and only a hex passes. So
  no argument of the form "the planner would refuse that machine anyway" holds,
  and none is made above.
* **Nothing ever invalidates a plate.** `PlateLibrary.invalidate` and
  `invalidate_profile` have no callers outside their own tests — no route, no
  worker, no event — so `status` is a constant `VALID` and `profile_version` is
  written and never read. The freshness guard above is a filter over a column
  nothing sets, and ADR-0006's staleness story is not implemented.

Refusing costs a cache miss and an engineer's click, which is what the farm did
last week. An unattended attach that might be wrong costs a machine, a spool and
a customer's order, and it costs them silently — an `EstimateVariance` with
`within_tolerance=True` is written either way. That asymmetry is why every unknown
here refuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from printorian.contexts.catalog import PreparedPlateView
from printorian.contexts.ordering.models import OrderLine
from printorian.core.colors import distinct_colors
from printorian.core.geometry import scaled_box


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why this plate may not be attached to this line unattended.

    A code plus the two facts that disagreed, so the log line says *what* was
    refused rather than only that something was. The codes are the same shape as
    every other refusal on this path (`intake.*`), and they are what somebody
    greps for when an order they expected to queue is sitting in prep.
    """

    code: str
    details: dict[str, Any] = field(default_factory=dict)


def admits(line: OrderLine, plate: PreparedPlateView) -> Refusal | None:
    """Whether ``plate`` may be attached to ``line`` with nobody looking.

    ``None`` means yes. Anything else is a refusal that leaves the job `PENDING`
    and the order in `PREP`, which is where every order went before #58 and where
    a person can see it.

    Pure on purpose — no session, no clock, no I/O — so the whole admission policy
    is one function a test can drive directly and a reader can hold in their head.
    Every check below is fatal, so the order between them decides only which code
    is logged first.
    """
    if plate.copies is None or plate.copies != line.quantity:
        # `attach_plate` writes the plate's minutes and grams onto the job as its
        # *whole* work, and `reprice.prepared_cost` divides those same totals by
        # `line.quantity` to get a per-unit figure. Both steps are assertions about
        # how many parts are on that bed; agreeing counts is the only thing that
        # makes either true. A one-up plate on a line of three prints a third of
        # what was sold, at a third of the work, and lands inside ADR-0013's band.
        #
        # `None` — a plate recorded before the column existed, or by an engineer
        # who did not say — is refused rather than assumed to be one. One is
        # exactly the value that makes the common case attach, so guessing it would
        # reinstate the failure this guard exists for.
        return Refusal(
            "intake.plate_layout_does_not_match_line",
            {"plate_copies": plate.copies, "line_quantity": line.quantity},
        )

    if plate.copies != 1:
        # **The bed's own footprint is recorded nowhere.** `copies` says how many
        # parts are on it and nothing says how they are arranged, so the only
        # geometry the planner ever sees is the job's — one part's box, written by
        # `intake._job_for`. `fleet.can_take`'s single geometric test then judges a
        # four-up bed by the footprint of one part, and a printer that cannot hold
        # the plate is eligible for it: a refused job at best, a head crash at
        # worst.
        #
        # This narrows what recording `copies` opened up — a line of three no
        # longer attaches to a three-up plate unattended — and the trade is taken
        # deliberately. The alternative was scaling the job's box by the copy
        # count, which is an invented number in the other direction: a 2x2
        # arrangement of four parts is twice the width, not four times, so the
        # planner would refuse machines that would have fitted it. `_dimensions`
        # makes the same argument about zeros.
        #
        # Closing it properly is two columns and a console field — the plate's own
        # bed extent, filled the way `copies` is — after which this check becomes
        # "the recorded footprint fits the machine" and multi-up plates attach
        # again.
        return Refusal("intake.plate_footprint_not_recorded", {"plate_copies": plate.copies})

    wanted = distinct_colors(line.colors)
    if wanted == 0 or wanted != len(plate.filament_grams):
        # Colour is in no term of `plate_key`, and `line.material_code` is not the
        # filament set: `_pricing_spec._material_price` picks the *dearest* of the
        # chosen specs and writes that one code onto the line. So a plate sliced
        # for white+black matches a white-only order on every key term there is,
        # attaches, reprices inside the band — the purge charge cancels, because
        # both sides of the difference use the order's own colours — and goes to a
        # machine that is asked to load one filament and handed a two-filament
        # plate. The mirror is worse commercially: a one-filament plate on a
        # two-colour order charges for a purge that never happens and ships a
        # mono-colour part.
        #
        # Slots rather than colours because slots are all the plate records, and
        # `distinct_colors` rather than `len` because two slots of white are one
        # filament — the rule `core.colors` exists to keep in one place.
        #
        # A line carrying no colours at all is refused here too: zero is "the
        # configurator recorded nothing", not "this plate needs no filament", and
        # `_quoted_spec` quietly prices such a line as `("default",)`.
        return Refusal(
            "intake.plate_filaments_do_not_match_line",
            {"plate_slots": len(plate.filament_grams), "line_colors": wanted},
        )

    if scaled_box(line.mesh, line.scale) is None:
        # The planner reads the job's width/depth/height, which `_dimensions`
        # leaves at zero for geometry nobody measured — and a zero reads as "fits
        # every machine". That default is right for the manual path, where a person
        # looks at the bed before the job is released, and it is CLAUDE.md §1's
        # invented number on this one, pointing at a machine too small for the
        # plate.
        #
        # It costs every line the web checkout places today, because
        # `CheckoutPage.tsx` sends no `mesh` even though the configurator has
        # measured one. That refusal is visible — the order lands in prep — and the
        # alternative is not.
        return Refusal("intake.line_geometry_not_measured", {"line_scale": str(line.scale)})

    if not plate.has_content:
        # A plate row can legitimately be numbers an engineer typed with no file
        # behind them, and `planning.plate_to_send` checks for the bytes at
        # dispatch. That check is too late for this path, and it fails in the one
        # direction that never recovers: the job goes READY, the order goes QUEUED,
        # the planner assigns a machine, dispatch answers `DISPATCH_NO_PLATE`, and
        # `service._return_to_queue` puts the job back — for ever. A paid order
        # then reads as queued, never prints, and never returns to `PREP`, which is
        # where every other refusal here deliberately lands it. An
        # `EstimateVariance` with `within_tolerance=True` has already been written
        # against it.
        return Refusal("intake.plate_has_no_content", {"plate_filename": plate.filename})

    return None


__all__ = ["Refusal", "admits"]
