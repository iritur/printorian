"""What a journal entry is, and the rules that do not need a database.

The journal is the farm's public argument in long form: the kit calls it «Журнал»
and its entries «отчёты» — reports, numbered in sequence, each one a piece of the
farm's own working shown with its figures. That framing is the design constraint.
A report has a number because it belongs to a series, and a section because the
index filters by one.

Everything here is pure, so the numbering rule and the reading estimate can be
tested without a session and cannot drift between the API and the service.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from statistics import median

#: Words a reader gets through in a minute.
#:
#: A convention, not a measurement — nobody has timed this farm's readers. It is
#: the usual figure for technical prose in Russian, and it is applied here rather
#: than stored so two reports of the same length never disagree about it. The
#: screen says «МИН ЧТЕНИЯ», which is honest about being an estimate.
WORDS_PER_MINUTE = 180

#: Even a two-paragraph note is a minute's read, never «0 МИН».
MIN_READ_MINUTES = 1

#: Two publications is the fewest that can describe a rhythm — one report has
#: no gap to measure, and a gap is what a cadence is made of.
MIN_DATES_FOR_RATE = 2

_SECONDS_PER_DAY = 86_400


class Section(StrEnum):
    """The kit's five filters over the index.

    Fixed rather than free tags: the index counts them and the counts are only
    meaningful over a closed set. A sixth section is a decision worth making
    deliberately, which is what adding a member here is.
    """

    COST = "cost"
    MATERIALS = "materials"
    FLEET = "fleet"
    ARCHITECTURE = "architecture"
    POSTPROCESSING = "postprocessing"


class BlockKind(StrEnum):
    """The shapes an article body is built from.

    Structured blocks rather than a blob of markup. Two reasons, and the second is
    the load-bearing one:

    1. The kit's article is not prose with formatting — it is prose *punctuated* by
       specific components: a rule callout, a pull quote with a citation, a code
       listing with its own header bar, a figures panel that reuses the pricing
       screen's leader rows. Markdown cannot express those without inventing a
       dialect and a renderer for it.
    2. Nothing here can carry raw HTML, so a published report cannot smuggle a
       script onto the storefront. An editor that accepted markup would put that
       decision in the hands of whoever holds `MANAGE_JOURNAL`.
    """

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    CALLOUT = "callout"
    QUOTE = "quote"
    CODE = "code"
    TABLE = "table"
    #: The kit's «Итог отчёта в цифрах» — leader rows and a headline total.
    FIGURES = "figures"


def weekly_rate(published: list[datetime]) -> Decimal | None:
    """How often the journal actually comes out, as reports per week.

    The kit prints «ВЫХОДИТ 1 / НЕД» and that is a claim about the farm's habits,
    so it is measured rather than declared: the *median* gap between consecutive
    publications, turned into a rate. The median and not the mean, because one
    three-month silence in an otherwise weekly journal should not halve the
    figure — it is one unusual gap, and the median is what ignores it.

    `None` when there is nothing to measure: fewer than two dated reports, or
    every one published on the same day. A journal that has not yet established a
    rhythm has no cadence, and «0 / НЕД» would read as "we have stopped".
    """
    dates = sorted(entry for entry in published if entry is not None)
    if len(dates) < MIN_DATES_FOR_RATE:
        return None

    gaps = [
        (later - earlier).total_seconds() / _SECONDS_PER_DAY for earlier, later in pairwise(dates)
    ]
    typical = median(gaps)
    if typical <= 0:
        return None

    return (Decimal(7) / Decimal(str(typical))).quantize(Decimal("0.1"))


def slugify(title: str) -> str:
    """A URL for a Russian title.

    Transliterated rather than percent-encoded: `/journal/chas-pechati` is a link
    somebody can read out loud, and an encoded Cyrillic path is forty characters of
    noise. Falls back to the report number when a title transliterates to nothing,
    which is what a title of pure punctuation does.
    """
    lowered = title.strip().lower()
    latin = "".join(_TRANSLITERATION.get(char, char) for char in lowered)
    cleaned = re.sub(r"[^a-z0-9]+", "-", latin).strip("-")
    return cleaned[:120]


def anchor_of(text: str, index: int) -> str:
    """The `id` a heading gets, and the target its contents entry links to.

    Suffixed with the position rather than deduplicated by lookup: two sections of
    a report can legitimately share a title, and a contents list whose second entry
    scrolls to the first is worse than one with an ugly anchor.
    """
    base = slugify(text) or "section"
    return f"{base}-{index + 1}"


def read_minutes(blocks: list[dict[str, object]]) -> int:
    """How long the body takes to read, from its own words.

    Computed rather than typed in by the author: a report that is edited after
    publication would otherwise keep advertising the old length, and nobody
    remembers to update it.
    """
    return max(MIN_READ_MINUTES, round(_word_count(blocks) / WORDS_PER_MINUTE))


def _word_count(blocks: list[dict[str, object]]) -> int:
    total = 0
    for block in blocks:
        for value in block.values():
            total += _words_in(value)
    return total


def _words_in(value: object) -> int:
    if isinstance(value, str):
        return len(value.split())
    if isinstance(value, list):
        return sum(_words_in(entry) for entry in value)
    if isinstance(value, dict):
        return sum(_words_in(entry) for entry in value.values())
    return 0


#: Cyrillic to Latin, for slugs. The GOST-style mapping a Russian reader expects
#: to see in a URL — «щ» is `shch`, not `w`.
_TRANSLITERATION = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}
