"""The journal, read by anyone and written by staff.

The split runs through every endpoint here as one parameter: `include_drafts`.
Reads pass it from the actor's permission, writes always set it — an editor has to
be able to fetch the draft they are editing. Nothing below decides visibility for
itself; the service takes the flag and the flag comes from the token.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from printorian.api.deps import AppClock, AppSettings, Journal, OptionalActor, requires
from printorian.api.routers._journal_feed import feed
from printorian.contexts.identity import Actor, Permission
from printorian.contexts.journal import (
    CreatePost,
    JournalIndex,
    PostCard,
    PostView,
    Section,
    Subscribe,
    Subscription,
    UpdatePost,
)

router = APIRouter(prefix="/journal", tags=["journal"])

#: A page of the index. Generous, because the kit's index is a grid plus an
#: archive list on one screen and paging through six cards would be absurd.
_PAGE = 24

#: How many reports the feed carries. A feed is a window on the recent, not an
#: archive — a reader that wants the back catalogue has the index for that, and
#: an unbounded feed grows until it times out.
_FEED_ITEMS = 20


def _may_edit(actor: Actor | None) -> bool:
    """Whether this caller may see and change unpublished reports."""
    return actor is not None and actor.can(Permission.MANAGE_JOURNAL)


@router.get("")
async def index(
    journal: Journal,
    actor: OptionalActor = None,
    section: Section | None = None,
    q: str = "",
    limit: Annotated[int, Query(ge=1, le=60)] = _PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JournalIndex:
    """The index screen: cards, and the counts its filter chips carry.

    Public. A reader who is not signed in sees exactly the published journal, and
    an editor sees their drafts inline with it — which is the point, because a
    draft is only useful if you can find it next to what it will sit beside.
    """
    return await journal.index(
        section=section,
        text=q,
        limit=limit,
        offset=offset,
        include_drafts=_may_edit(actor),
    )


@router.get("/latest")
async def latest(journal: Journal) -> PostCard | None:
    """The index's featured report, or `null` for a journal with nothing out yet.

    No actor, on purpose: this answers the same for everyone. The lead block bills
    it as the issue's main article, and a draft is not that for anybody — including
    the person writing it.
    """
    return await journal.latest()


@router.get("/rss", response_class=Response)
async def rss(journal: Journal, clock: AppClock, settings: AppSettings) -> Response:
    """The journal as a feed, for «Подписаться на RSS».

    Published reports only, whoever is asking — a feed is a public document and a
    draft that reached one cannot be recalled from the readers that already
    fetched it.

    Item links come from `settings.site_url`, not from the request. A proxy
    rewrites the Host on its way here, so a request-derived origin is the API's
    own address and every subscriber would be handed a link into this JSON rather
    than to the article.
    """
    rows = await journal.index(limit=_FEED_ITEMS, include_drafts=False)
    site = settings.site_url.rstrip("/")

    return Response(
        content=feed(rows.rows, site=site, built_at=clock.now()),
        media_type="application/rss+xml; charset=utf-8",
        # A quarter of an hour: long enough that a reader polling every few
        # minutes costs nothing, short enough that a new report is not stale news
        # by the time it arrives.
        headers={"Cache-Control": "public, max-age=900"},
    )


@router.post("/subscribe", status_code=status.HTTP_202_ACCEPTED)
async def subscribe(data: Subscribe, journal: Journal) -> Subscription:
    """Ask to hear when a report goes out.

    Public and unauthenticated: most people who read a farm's journal have no
    account, and requiring one would be the friction the storefront exists to
    remove. 202 rather than 201 — the address is recorded, and nothing has been
    sent to it yet.

    Declared above `/{slug}` so the path parameter cannot swallow it.
    """
    return await journal.subscribe(str(data.email))


@router.post("/unsubscribe/{token}", status_code=status.HTTP_202_ACCEPTED)
async def unsubscribe(token: str, journal: Journal) -> Subscription:
    """The card's «отписка в один клик», honoured without an account.

    The token is the whole authorisation. That is deliberate: making somebody sign
    in to stop receiving mail they never signed in to request is how a newsletter
    becomes something people report as spam.
    """
    return await journal.unsubscribe(token)


@router.get("/{slug}")
async def read(slug: str, journal: Journal, actor: OptionalActor = None) -> PostView:
    """One report, with its contents list and the rail of neighbours.

    A draft answers 404 to everyone else — not 403. The difference matters: 403
    would confirm the slug exists, which is enough to enumerate unpublished work by
    guessing titles.
    """
    return await journal.get(slug, include_drafts=_may_edit(actor))


# Everything below is staff-only, gated on `MANAGE_JOURNAL` — Engineer and above.


@router.post("", status_code=status.HTTP_201_CREATED)
async def write(
    data: CreatePost,
    journal: Journal,
    _: Annotated[Actor, Depends(requires(Permission.MANAGE_JOURNAL))],
) -> PostView:
    """Start a report. Draft unless the author says otherwise."""
    return await journal.create(data)


@router.patch("/{slug}", dependencies=[Depends(requires(Permission.MANAGE_JOURNAL))])
async def edit(slug: str, data: UpdatePost, journal: Journal) -> PostView:
    """Change a report, including publishing and unpublishing it."""
    return await journal.update(slug, data)


@router.delete(
    "/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(requires(Permission.MANAGE_JOURNAL))],
)
async def remove(slug: str, journal: Journal) -> None:
    """Delete a report outright.

    Kept rather than soft-deleted: a report is editorial, not a record of anything
    that happened, so there is nothing to preserve for an audit. Unpublishing is
    the reversible option and it is one field away.
    """
    await journal.delete(slug)
