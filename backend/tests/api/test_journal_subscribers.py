"""The journal's subscription list.

Separate from the reports because it answers a different question: not what the
farm published, but who asked to hear about it. The rule under most of these is
that the endpoint must never become a way of asking whether a *particular person*
subscribed — so every path answers the same, whatever it found.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._journal_support import a_journal, subscriber_count, unsubscribe_token


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


async def test_anyone_may_subscribe(client: AsyncClient) -> None:
    """No account. Most people who read a farm's journal will never have one."""
    response = await client.post("/journal/subscribe", json={"email": "reader@example.com"})

    assert response.status_code == 202
    assert response.json()["accepted"] is True


async def test_subscribing_twice_says_the_same_thing(
    client: AsyncClient, settings: Settings
) -> None:
    """A different answer for "already on the list" is a way to ask about someone."""
    first = await client.post("/journal/subscribe", json={"email": "twice@example.com"})
    second = await client.post("/journal/subscribe", json={"email": "TWICE@example.com"})

    assert first.json() == second.json()
    # Folded, so the two spellings are one subscription rather than two letters.
    assert await subscriber_count(settings) == 1


async def test_a_malformed_address_is_refused(client: AsyncClient) -> None:
    assert (
        await client.post("/journal/subscribe", json={"email": "not-an-address"})
    ).status_code == 422


async def test_the_one_click_link_works_without_an_account(
    client: AsyncClient, settings: Settings
) -> None:
    """The card promises «отписка в один клик», and the token is the whole of it.

    Requiring a sign-in to stop mail nobody signed in to request is how a
    newsletter becomes something people report as spam.
    """
    await client.post("/journal/subscribe", json={"email": "gone@example.com"})
    token = await unsubscribe_token(settings, "gone@example.com")

    assert (await client.post(f"/journal/unsubscribe/{token}")).status_code == 202
    assert await subscriber_count(settings, active_only=True) == 0


async def test_an_unknown_token_answers_the_same(client: AsyncClient, settings: Settings) -> None:
    """So probing tokens teaches a stranger nothing."""
    await client.post("/journal/subscribe", json={"email": "stays@example.com"})

    good = await client.post(
        f"/journal/unsubscribe/{await unsubscribe_token(settings, 'stays@example.com')}"
    )
    bad = await client.post("/journal/unsubscribe/nonsense")

    assert good.status_code == bad.status_code
    assert good.json() == bad.json()


async def test_changing_your_mind_brings_you_back(client: AsyncClient, settings: Settings) -> None:
    """Refusing would leave somebody who opted out no way to return."""
    await client.post("/journal/subscribe", json={"email": "back@example.com"})
    await client.post(
        f"/journal/unsubscribe/{await unsubscribe_token(settings, 'back@example.com')}"
    )

    await client.post("/journal/subscribe", json={"email": "back@example.com"})

    assert await subscriber_count(settings, active_only=True) == 1
    # One row throughout: the opt-out is remembered, not replaced by a fresh insert.
    assert await subscriber_count(settings) == 1
