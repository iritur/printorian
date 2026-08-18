"""The journal as RSS.

Its own file because a feed is a different kind of thing from the rest of the
journal API: not JSON for our client to render, but a finished document somebody
else's reader will parse. That is why these tests assert on *text* — the escaping,
the date format and the link targets are the contract, and all three are the usual
ways a feed silently stops working.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._journal_support import a_journal, token_for, write


@pytest.fixture
async def client(
    object_store: InMemoryObjectStore,
    settings: Settings,
    clock: FixedClock,
    bus: EventBus,
    clean_database: None,
) -> AsyncIterator[AsyncClient]:
    async for http in a_journal(object_store, settings, clock, bus):
        yield http


async def test_the_feed_carries_published_reports(client: AsyncClient) -> None:
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, title="Вышел", is_published=True)

    response = await client.get("/journal/rss")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/rss+xml")
    assert "<title>Вышел</title>" in response.text


async def test_a_draft_never_reaches_the_feed(client: AsyncClient) -> None:
    """A feed is public and cannot be recalled from readers that already fetched it."""
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, title="Черновик")

    for headers in ({}, auth):
        assert "Черновик" not in (await client.get("/journal/rss", headers=headers)).text


async def test_items_link_to_the_storefront_not_the_api(
    client: AsyncClient, settings: Settings
) -> None:
    """A reader clicking through wants the article, not its JSON."""
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, is_published=True)

    body = (await client.get("/journal/rss")).text

    site = settings.site_url.rstrip("/")
    assert f"<link>{site}/journal/chas-pechati</link>" in body
    # `isPermaLink` on the address, so editing a report does not make readers
    # show it a second time as though it were new.
    assert f'<guid isPermaLink="true">{site}/journal/chas-pechati</guid>' in body
    # Never this API. A subscriber clicking through wants the page.
    assert "/api/journal/chas-pechati" not in body


async def test_the_feed_escapes_what_an_author_typed(client: AsyncClient) -> None:
    """An unescaped `&` is the usual way a feed stops parsing entirely."""
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, title="Сопло & ремни <тест>", is_published=True)

    body = (await client.get("/journal/rss")).text

    assert "Сопло &amp; ремни &lt;тест&gt;" in body
    assert "<тест>" not in body


async def test_dates_are_readable_by_a_reader(client: AsyncClient) -> None:
    """RFC 822, in English whatever locale the server runs under."""
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, is_published=True)

    body = (await client.get("/journal/rss")).text

    assert re.search(
        r"<pubDate>(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} [A-Z][a-z]{2} \d{4} "
        r"\d{2}:\d{2}:\d{2} \+0000</pubDate>",
        body,
    ), body


async def test_an_empty_journal_still_answers_a_valid_channel(client: AsyncClient) -> None:
    """A reader subscribing before the first report should not get a 500."""
    body = (await client.get("/journal/rss")).text

    assert body.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert "<channel>" in body and "</rss>" in body
    assert "<item>" not in body
