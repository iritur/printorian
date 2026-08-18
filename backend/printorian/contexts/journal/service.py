"""Reading and writing the journal.

The one rule worth stating up front: **`include_drafts` is a parameter of every
read, and it defaults to false.** A draft is invisible unless the caller has
already proved it may see one, and the check lives at the API edge where the actor
is. A service that decided for itself would have to know about permissions, and a
service that defaulted to showing everything would leak a half-written report the
first time somebody forgot an argument.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from printorian.contexts.journal.models import JournalPost, JournalSubscriber
from printorian.contexts.journal.policies import Section, anchor_of, slugify, weekly_rate
from printorian.contexts.journal.schemas import (
    CreatePost,
    JournalIndex,
    Neighbour,
    PostCard,
    PostView,
    SectionCount,
    Subscription,
    TocEntry,
    UpdatePost,
)
from printorian.core.clock import Clock
from printorian.core.errors import NotFoundError, ValidationError
from printorian.core.ids import new_id

#: How many other reports the article rail offers. Three, as the kit draws it —
#: enough to suggest the series continues, few enough not to compete with the
#: article it sits beside.
NEIGHBOURS = 3


class JournalService:
    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    # ------------------------------------------------------------- reading

    async def index(
        self,
        *,
        section: Section | None = None,
        text: str = "",
        limit: int = 24,
        offset: int = 0,
        include_drafts: bool = False,
    ) -> JournalIndex:
        """The index screen: rows, plus the counts its filter chips show.

        The counts are taken over the whole journal and not over `rows`. A chip
        that said «Себестоимость 3» because three happened to be on this page would
        be describing the page, and the reader would reasonably conclude the farm
        has written three reports about cost.
        """
        rows = (
            await self._session.scalars(
                self._filtered(section=section, text=text, include_drafts=include_drafts)
                .order_by(JournalPost.number.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()

        # Counted without the section filter: the chips have to keep showing what
        # the other sections hold, or picking one would empty the row that lets you
        # pick a different one.
        tally = self._filtered(text=text, include_drafts=include_drafts).subquery()
        counted = await self._session.execute(
            select(tally.c.section, func.count()).group_by(tally.c.section)
        )
        counts = [SectionCount(section=Section(name), count=n) for name, n in counted.all()]

        total = await self._session.scalar(select(func.count()).select_from(tally)) or 0

        # Published only, and deliberately a different number from `total`.
        # «ВЫПУСКОВ» means issues that came out; «ВСЕ N» on the filter chip means
        # what the chip will show you. For a reader they are the same figure, and
        # for an editor with a draft open they are not — which is the honest
        # answer to both questions rather than one answer bent to serve both.
        published = (
            await self._session.scalar(
                select(func.count())
                .select_from(JournalPost)
                .where(JournalPost.is_published.is_(True))
            )
            or 0
        )

        # Measured over published reports only, and over all of them rather than
        # this page: a cadence describes the journal's habits, and the habits do
        # not change because somebody filtered by section.
        dates = (
            await self._session.scalars(
                select(JournalPost.published_at)
                .where(JournalPost.is_published.is_(True))
                .where(JournalPost.published_at.is_not(None))
            )
        ).all()

        return JournalIndex(
            rows=[PostCard.model_validate(row) for row in rows],
            counts=sorted(counts, key=lambda entry: entry.section.value),
            total=int(total),
            published_total=int(published),
            weekly_rate=weekly_rate([entry for entry in dates if entry is not None]),
        )

    async def get(self, slug: str, *, include_drafts: bool = False) -> PostView:
        post = await self._find(slug, include_drafts=include_drafts)
        view = PostView.model_validate(post)
        view.toc = _contents(post.blocks)
        view.neighbours = await self._neighbours(post, include_drafts=include_drafts)
        return view

    async def latest(self) -> PostCard | None:
        """The index's featured report: the newest **published** one.

        Takes no `include_drafts`, unlike every other read here, and that is the
        point. The lead block calls this «ГЛАВНЫЙ МАТЕРИАЛ ВЫПУСКА» — a claim about
        what the farm has put out — so an editor's half-written draft must not
        occupy it even in their own browser. Seeing a draft *in the grid* is useful
        because it sits where it will sit; seeing it presented as the issue's lead
        article is just wrong, and wrong in a way that is easy to publish by
        mistake.

        `None` for a journal with nothing published, so the screen omits the whole
        lead block rather than framing nothing.
        """
        found = await self._session.scalar(self._filtered().order_by(JournalPost.number.desc()))
        return PostCard.model_validate(found) if found else None

    # ------------------------------------------------------------- writing

    async def create(self, data: CreatePost) -> PostView:
        number = (await self._session.scalar(select(func.max(JournalPost.number))) or 0) + 1
        post = JournalPost(
            number=number,
            slug=await self._free_slug(slugify(data.title) or f"report-{number}"),
            title=data.title,
            lede=data.lede,
            excerpt=data.excerpt,
            section=data.section,
            author=data.author,
            data_note=data.data_note,
            blocks=[block.model_dump() for block in data.blocks],
            is_published=data.is_published,
            published_at=self._clock.now() if data.is_published else None,
        )
        self._session.add(post)
        await self._session.commit()
        return await self.get(post.slug, include_drafts=True)

    async def update(self, slug: str, data: UpdatePost) -> PostView:
        post = await self._find(slug, include_drafts=True)
        patch = data.model_dump(exclude_unset=True)

        if "blocks" in patch:
            post.blocks = [block.model_dump() for block in (data.blocks or [])]
            patch.pop("blocks")
        if "is_published" in patch and data.is_published:
            # Stamped once, on the first publication. A report pulled down and put
            # back is not new, and re-dating it would move it to the top of an index
            # readers have already scrolled past.
            post.published_at = post.published_at or self._clock.now()
        for field, value in patch.items():
            setattr(post, field, value)

        await self._session.commit()
        return await self.get(post.slug, include_drafts=True)

    async def subscribe(self, email: str) -> Subscription:
        """Add an address to the journal's list, or leave it where it is.

        Idempotent, and silent about which of the two happened. Re-subscribing an
        address that opted out *does* bring it back — that is the person changing
        their mind, and refusing would leave them no way to return.
        """
        folded = email.strip().lower()
        found = await self._session.scalar(
            select(JournalSubscriber).where(JournalSubscriber.email == folded)
        )
        if found is None:
            self._session.add(
                JournalSubscriber(
                    email=folded,
                    # A UUIDv7 hex, which is unguessable enough for a link whose
                    # worst case is unsubscribing somebody who wanted to stay.
                    token=new_id().hex,
                    subscribed_at=self._clock.now(),
                )
            )
        else:
            found.unsubscribed_at = None
        await self._session.commit()
        return Subscription()

    async def unsubscribe(self, token: str) -> Subscription:
        """Honour the one-click link the card promises.

        A bad token answers the same as a good one. Somebody who has already
        unsubscribed clicking the link a second time should see it work, and a
        stranger probing tokens should learn nothing from the difference.
        """
        found = await self._session.scalar(
            select(JournalSubscriber).where(JournalSubscriber.token == token)
        )
        if found is not None and found.is_active:
            found.unsubscribed_at = self._clock.now()
            await self._session.commit()
        return Subscription()

    async def delete(self, slug: str) -> None:
        post = await self._find(slug, include_drafts=True)
        await self._session.delete(post)
        await self._session.commit()

    # ------------------------------------------------------------ internals

    def _filtered(
        self,
        *,
        section: Section | None = None,
        text: str = "",
        include_drafts: bool = False,
    ) -> Select[tuple[JournalPost]]:
        query = select(JournalPost)
        if not include_drafts:
            query = query.where(JournalPost.is_published.is_(True))
        if section is not None:
            query = query.where(JournalPost.section == section)
        if text.strip():
            # Matched against the folded column, so a Russian query finds a Russian
            # title whatever case either is in.
            query = query.where(JournalPost.search_text.like(f"%{text.strip().lower()}%"))
        return query

    async def _find(self, slug: str, *, include_drafts: bool) -> JournalPost:
        found = await self._session.scalar(
            self._filtered(include_drafts=include_drafts).where(JournalPost.slug == slug)
        )
        if found is None:
            # The same answer for "no such report" and "a draft you may not see".
            # Distinguishing them would let anyone enumerate unpublished work by
            # watching which slugs 403 instead of 404.
            raise NotFoundError("error.journal.not_found", slug=slug)
        return found

    async def _free_slug(self, base: str) -> str:
        """A slug nobody is using, suffixed only if the plain one is taken."""
        taken = set(
            (
                await self._session.scalars(
                    select(JournalPost.slug).where(JournalPost.slug.like(f"{base}%"))
                )
            ).all()
        )
        if base not in taken:
            return base
        for suffix in range(2, 100):
            candidate = f"{base}-{suffix}"
            if candidate not in taken:
                return candidate
        raise ValidationError("error.journal.slug_exhausted", slug=base)

    async def _neighbours(self, post: JournalPost, *, include_drafts: bool) -> list[Neighbour]:
        rows = (
            await self._session.scalars(
                self._filtered(include_drafts=include_drafts)
                .where(JournalPost.id != post.id)
                .order_by(JournalPost.number.desc())
                .limit(NEIGHBOURS)
            )
        ).all()
        return [Neighbour(slug=row.slug, number=row.number, title=row.title) for row in rows]


def _contents(blocks: list[dict[str, Any]]) -> list[TocEntry]:
    """«Содержание», derived from the headings the article actually has.

    Position-indexed anchors, so two sections that share a title still scroll to
    the right one — see `anchor_of`.
    """
    return [
        TocEntry(anchor=anchor_of(str(block.get("text", "")), index), text=str(block["text"]))
        for index, block in enumerate(blocks)
        if block.get("kind") == "heading" and block.get("text")
    ]
