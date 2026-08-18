"""Journal — the farm's public reports, numbered and filed by section.

Editorial, not operational: nothing in here affects what a machine does or what an
order costs. It exists because the storefront's whole argument is that the farm's
figures can be checked, and a journal is where that argument is made at length.

Public reads, gated writes. Anyone may read a *published* report; drafts and every
kind of edit need `MANAGE_JOURNAL`, which sits at the same tier as
`MANAGE_LIBRARY` — both are the shop window, and the same people curate both.
"""

from printorian.contexts.journal.models import JournalPost, JournalSubscriber, search_text_of
from printorian.contexts.journal.policies import (
    MIN_READ_MINUTES,
    WORDS_PER_MINUTE,
    BlockKind,
    Section,
    anchor_of,
    read_minutes,
    slugify,
    weekly_rate,
)
from printorian.contexts.journal.schemas import (
    Block,
    CreatePost,
    JournalIndex,
    Neighbour,
    PostCard,
    PostView,
    SectionCount,
    Subscribe,
    Subscription,
    TocEntry,
    UpdatePost,
)
from printorian.contexts.journal.service import NEIGHBOURS, JournalService

__all__ = [
    "MIN_READ_MINUTES",
    "NEIGHBOURS",
    "WORDS_PER_MINUTE",
    "Block",
    "BlockKind",
    "CreatePost",
    "JournalIndex",
    "JournalPost",
    "JournalService",
    "JournalSubscriber",
    "Neighbour",
    "PostCard",
    "PostView",
    "Section",
    "SectionCount",
    "Subscribe",
    "Subscription",
    "TocEntry",
    "UpdatePost",
    "anchor_of",
    "read_minutes",
    "search_text_of",
    "slugify",
    "weekly_rate",
]
