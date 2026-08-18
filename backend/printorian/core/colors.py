"""What "how many colours" means.

A plate carries one colour per slot, and two slots may hold the same one — a
customer who asks for two colours and then picks white for both has made a
single-colour plate, whatever the slot count says.

Three separate decisions turn on that number and all three were counting slots:

* **pricing** charges purge waste per extra colour, but purge is spent flushing
  the nozzle *between different filaments*. Two slots of white flush nothing.
* **fleet** requires a multi-material machine for more than one colour, but a
  plate that uses one filament prints on any machine.
* **scheduling** protects scarce multi-material capacity from single-colour work,
  which is exactly what a plate of two identical slots is.

Counting slots overcharges the customer for waste that never happens, then
refuses the machines that could have printed it. So the rule lives here, once,
rather than as three copies of ``len(set(...))`` that can drift apart again.

Comparison is case-insensitive: ``"White"`` and ``"white"`` name one filament,
and a plate should not be billed for a purge because two screens disagreed about
capitalisation.
"""

from __future__ import annotations

from collections.abc import Iterable


def distinct_colors(colors: Iterable[str]) -> int:
    """How many different filaments a plate actually uses."""
    return len({colour.casefold() for colour in colors})


def extra_colors(colors: Iterable[str]) -> int:
    """Filament changes a plate needs — one fewer than the colours it uses."""
    return max(0, distinct_colors(colors) - 1)


def is_multicolor(colors: Iterable[str]) -> bool:
    """Whether the plate needs a machine that can hold more than one filament."""
    return distinct_colors(colors) > 1


__all__ = ["distinct_colors", "extra_colors", "is_multicolor"]
