"""What packing is measured by, and how a box gets chosen.

Three rules live here, and all three exist because the alternative is a person
guessing under time pressure:

**The clock the post works to is the courier's, not the customer's.** A promise
three days out is irrelevant at 17:20 when the van comes at 19:30 — everything
in today's pickup is due at the same instant, and a queue sorted by anything
else ships the wrong parcel first.

**The box is chosen by geometry, not by eye.** Picking one size larger "to be
safe" costs filler, volumetric weight and margin on every order, and nobody sees
the total until the month closes.

**The wrap is decided by the part, not by the packer.** Both damages the farm
recorded in July were unwrapped thin-walled parts. That is now a rule with a
threshold rather than a sentence in an instruction somebody skims.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple

from printorian.core.errors import DomainRuleViolationError


class TaraKind(StrEnum):
    """What a stocked packing item *is*, which decides how it is measured.

    A box has inner dimensions and is consumed one per parcel; a roll of film has
    neither and is consumed by the metre. One table with a nullable geometry and
    a `kind` beats two tables that would both need the same consumption ledger.
    """

    BAG = "bag"
    BOX = "box"
    WRAP = "wrap"
    FILLER = "filler"


#: Kinds that enclose a parcel, and so can be *chosen* for one. A roll of film is
#: stocked and consumed like the rest but is never the answer to "which box".
ENCLOSURES: frozenset[TaraKind] = frozenset({TaraKind.BAG, TaraKind.BOX})


class PackStatus(StrEnum):
    """Where one order is between "inspected" and "handed to the courier"."""

    #: Every finishing task passed QC. The parcel is the post's to make.
    CHECKED = "checked"
    #: A packer is on it and the clock is running.
    PACKING = "packing"
    #: Cannot be closed for a reason outside the post — see `HoldReason`.
    #: Its own column rather than a flag on `CHECKED`, because it is the one
    #: column where the fix is somebody else's and staring at it does not help.
    HELD = "held"
    #: Sealed, weighed, labelled. Waiting for the van.
    READY = "ready"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {PackStatus.SHIPPED, PackStatus.CANCELLED}


#: The only moves the system performs. A parcel can be held from anywhere short of
#: shipped, because the invoice can go unpaid at any point before the van leaves.
TRANSITIONS: dict[PackStatus, frozenset[PackStatus]] = {
    PackStatus.CHECKED: frozenset({PackStatus.PACKING, PackStatus.HELD, PackStatus.CANCELLED}),
    PackStatus.PACKING: frozenset({PackStatus.READY, PackStatus.HELD, PackStatus.CANCELLED}),
    # Back to `CHECKED`, never straight to `PACKING`: whoever cleared the hold is
    # rarely the packer, and the parcel goes back in the queue to be picked up.
    PackStatus.HELD: frozenset({PackStatus.CHECKED, PackStatus.CANCELLED}),
    PackStatus.READY: frozenset({PackStatus.SHIPPED, PackStatus.HELD, PackStatus.CANCELLED}),
    PackStatus.SHIPPED: frozenset(),
    PackStatus.CANCELLED: frozenset(),
}


def can_transition(current: PackStatus, target: PackStatus) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: PackStatus, target: PackStatus) -> None:
    if not can_transition(current, target):
        raise DomainRuleViolationError(
            "error.packaging.invalid_transition",
            current=current.value,
            target=target.value,
        )


class HoldReason(StrEnum):
    """Why a parcel cannot be closed. A code the client renders (ADR-0012)."""

    INVOICE_UNPAID = "invoice_unpaid"
    WAYBILL_MISSING = "waybill_missing"
    ADDRESS_INCOMPLETE = "address_incomplete"
    ITEM_MISSING = "item_missing"


class Dims(NamedTuple):
    """A bounding box in millimetres. The axis names carry no meaning — see `sorted_mm`."""

    length_mm: Decimal
    width_mm: Decimal
    height_mm: Decimal

    @property
    def sorted_mm(self) -> tuple[Decimal, Decimal, Decimal]:
        """Smallest to largest.

        Everything here compares boxes in this form because a parcel can be turned
        over. Comparing x-to-x would refuse a box that obviously fits, and a rule
        that is visibly wrong on the floor is a rule that gets ignored.
        """
        smallest, middle, largest = sorted((self.length_mm, self.width_mm, self.height_mm))
        return smallest, middle, largest

    @property
    def volume_cm3(self) -> Decimal:
        return (self.length_mm * self.width_mm * self.height_mm) / Decimal(1000)


def fits(inner: Dims, batch: Dims) -> bool:
    """Whether `batch` goes inside `inner`, in any orientation."""
    return all(room >= need for room, need in zip(inner.sorted_mm, batch.sorted_mm, strict=True))


def stack_box(part: Dims, quantity: int) -> Dims:
    """One line's parts laid flat on top of each other.

    The crude rule, stated crudely: multiply the *thinnest* axis by the count.
    Ten brackets 190 × 140 × 7 become 190 × 140 × 70, which is what a packer
    actually does with ten identical flat parts.

    It is wrong for anything that nests, and deliberately wrong in the safe
    direction — it over-estimates, so the chosen box is never too small. The
    recorded gap between this figure and the box the packer really used is what
    will eventually replace it; the screen reports that accuracy as a percentage
    for exactly that reason.
    """
    thinnest, middle, longest = part.sorted_mm
    return Dims(longest, middle, thinnest * Decimal(max(1, quantity)))


def batch_box(stacks: Sequence[Dims]) -> Dims:
    """Several stacks stood together in one parcel.

    The same rule one level up: the stacks sit on each other, so the footprint is
    the largest of theirs and the depth is the sum. Over-estimates for anything
    that could go side by side, which is again the safe direction.
    """
    if not stacks:
        return Dims(Decimal(0), Decimal(0), Decimal(0))
    depth = sum((stack.sorted_mm[0] for stack in stacks), Decimal(0))
    width = max(stack.sorted_mm[1] for stack in stacks)
    length = max(stack.sorted_mm[2] for stack in stacks)
    return Dims(length, width, depth)


#: Carriers bill the greater of real and volumetric weight, and 5000 is the
#: divisor every carrier the farm ships with applies. A constant rather than a
#: per-carrier column until one of them disagrees.
DIM_DIVISOR = Decimal(5000)


def volumetric_grams(dims: Dims, *, divisor: Decimal = DIM_DIVISOR) -> Decimal:
    """What the carrier will charge this parcel as, if it is bigger than it is heavy.

    Shown beside the real weight rather than instead of it: a packer who can see
    both knows whether squeezing into the next box down actually saves anything.
    """
    if divisor <= 0:  # pragma: no cover - a zero divisor is a configuration error
        return Decimal(0)
    return (dims.volume_cm3 / divisor * Decimal(1000)).quantize(Decimal("0.1"))


def chargeable_grams(real_grams: Decimal, dims: Dims) -> Decimal:
    """The greater of the two. What the shipping line in the estimate is built on."""
    return max(real_grams, volumetric_grams(dims))


#: Walls thinner than this travel wrapped, whatever the packer thinks. Set from
#: the two damages in July, both of which were unwrapped parts at 0.6 mm.
THIN_WALL_MM = Decimal("0.8")


def needs_wrap(min_wall_mm: Decimal | None) -> bool:
    """Whether film is mandatory here. Unmeasured geometry is not a licence to skip it."""
    if min_wall_mm is None:
        return False
    return min_wall_mm < THIN_WALL_MM


#: Inside this many minutes of the courier, a parcel is drawn as urgent. One long
#: pack plus its label — the point past which starting a different parcel first
#: makes this one miss the van.
SOON_MINUTES = 120

#: How far back the tara table, the pace figures and the 30-day panel look.
STATS_DAYS = 30


__all__ = [
    "DIM_DIVISOR",
    "ENCLOSURES",
    "SOON_MINUTES",
    "STATS_DAYS",
    "THIN_WALL_MM",
    "TRANSITIONS",
    "Dims",
    "HoldReason",
    "PackStatus",
    "TaraKind",
    "assert_transition",
    "batch_box",
    "can_transition",
    "chargeable_grams",
    "fits",
    "needs_wrap",
    "stack_box",
    "volumetric_grams",
]
