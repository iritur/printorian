"""The journal's one table.

One table on purpose. A report has a body, and the body is a list of blocks that
is only ever read and written whole — there is no query that wants the third
paragraph of report #57 without the rest of it. Splitting blocks into rows would
buy nothing and cost a join on every read of every article.

JSONB rather than JSON (ADR-0017), so the column can be indexed and queried later
if a "reports mentioning PETG-CF" search ever earns its place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Index, Integer, String, event
from sqlalchemy.orm import Mapped, mapped_column

from printorian.contexts.journal.policies import Section, read_minutes, slugify
from printorian.core.db import Base, Entity, JsonB, UtcDateTime, enum_column


class JournalPost(Entity):
    """One numbered report.

    Draft by default. A report becomes visible to the public the moment somebody
    publishes it and not before — which is why `is_published` is separate from
    `published_at` rather than inferred from it: unpublishing must not erase the
    date it originally went out, or a re-published report claims to be new.
    """

    __tablename__ = "journal_posts"
    __table_args__ = (
        # The index is over the pair the storefront actually orders by. A published
        # index reads newest-first and never sees drafts, so both columns are in
        # every query it makes.
        Index("ix_journal_posts_published", "is_published", "number"),
        Index("ix_journal_posts_section", "section"),
    )

    #: «ОТЧЁТ :: #57». Unique, because the series is how the journal refers to
    #: itself — the article's own chrome and its neighbours are addressed by it.
    number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(140), unique=True, nullable=False)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    #: The article header's standfirst, under the display title.
    lede: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    #: The index card's summary. Separate from `lede` because a card is read at a
    #: glance in a grid and a standfirst is read at the top of an article — the
    #: same sentence rarely does both jobs well.
    excerpt: Mapped[str] = mapped_column(String(600), nullable=False, default="")

    section: Mapped[Section] = mapped_column(enum_column(Section), nullable=False)

    #: The body, as `BlockKind`-tagged blocks. Read and written whole.
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JsonB, nullable=False, default=list)

    #: Who wrote it, as the kit prints it — «ИНЖЕНЕРНАЯ ГРУППА · ФЕРМА KN-SOL.21».
    #: Free text and not a user id: reports are signed by a group as often as by a
    #: person, and attributing one to an account would then be a lie.
    author: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    #: The header's «ДАННЫЕ :: 12 ПРИНТЕРОВ · 90 СУТОК». Empty for a report that
    #: rests on no dataset, and then the line is absent rather than blank.
    data_note: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    #: Derived from `blocks` by the mapper event below, never supplied by a client.
    read_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: When it first went out. Kept across an unpublish, so the date on a restored
    #: report is the one readers already saw.
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    #: Lowercased title and excerpt, for the index's search box.
    #:
    #: A column rather than `lower(title) LIKE`, for the reason the catalogue
    #: learned the hard way: SQL `lower()` is ASCII-only in some engines, so
    #: «Себестоимость» never matched «себестоимость». Folding it in Python once,
    #: here, makes the search work in Russian.
    search_text: Mapped[str] = mapped_column(String(1000), nullable=False, default="")


def search_text_of(title: str, excerpt: str, author: str) -> str:
    """What the index's search box matches against."""
    return " ".join(part.lower() for part in (title, excerpt, author) if part)


@event.listens_for(JournalPost, "before_insert")
@event.listens_for(JournalPost, "before_update")
def _derive(_mapper: object, _connection: object, target: JournalPost) -> None:
    """Keep the derived columns honest on every write.

    A mapper event rather than a service call, so a report written by a migration,
    a seed script or a future admin tool cannot end up with a stale reading time or
    a search index that does not match its title.
    """
    target.search_text = search_text_of(target.title, target.excerpt, target.author)
    target.read_minutes = read_minutes(target.blocks or [])
    if not target.slug:
        target.slug = slugify(target.title) or f"report-{target.number}"


class JournalSubscriber(Base):
    """Somebody who asked to be told when a report goes out.

    Its own table rather than a flag on `User`: most people who want the journal
    have no account and never will, and requiring one to read a farm's blog would
    be the kind of friction the storefront exists to avoid.

    **No mail is sent from here yet.** This records the request and the means to
    withdraw it, which is what the screen actually promises the reader can do; the
    dispatch side needs a mailer the deployment does not have. That gap is real
    and is written down rather than papered over — see `docs/DESIGN-KIT-
    INTEGRATION.md`.
    """

    __tablename__ = "journal_subscribers"

    #: The address, folded to lower case. Unique, because subscribing twice is one
    #: subscription — and because the endpoint answers the same either way, so a
    #: stranger cannot use it to find out who is on the list.
    email: Mapped[str] = mapped_column(String(320), primary_key=True)

    #: The one-click unsubscribe link's secret. Random and per-subscriber, so a
    #: link cannot be guessed from an address, and unsubscribing needs no account.
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    subscribed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    #: Set rather than deleting the row. A resubscribe should not be able to
    #: silently undo someone's opt-out by racing a fresh insert against it, and a
    #: withdrawn address is the one thing worth remembering about a stranger.
    unsubscribed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    @property
    def is_active(self) -> bool:
        return self.unsubscribed_at is None
