"""The journal over HTTP.

Two things are worth pinning here and the rest follows from them.

**A draft is invisible.** Not merely unlisted — unreachable, and by 404 rather than
403, because a 403 confirms the slug exists and that is enough to enumerate
unpublished work by guessing titles.

**Numbering belongs to the series.** Nothing a client sends decides a report's
number, so two people writing at once cannot collide on one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from httpx import AsyncClient

from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus
from printorian.core.storage import InMemoryObjectStore
from tests.api._journal_support import a_journal, a_report, backdate, token_for, write


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


# ------------------------------------------------------------------ writing


async def test_the_series_numbers_itself(client: AsyncClient) -> None:
    """A client never names a number, so two editors cannot pick the same one."""
    auth = await token_for(client, "editor@example.com")

    first = await write(client, auth, title="Первый")
    second = await write(client, auth, title="Второй")

    assert first["number"] == 1
    assert second["number"] == 2


async def test_a_russian_title_becomes_a_readable_slug(client: AsyncClient) -> None:
    """Transliterated, not percent-encoded — a link somebody can read out loud."""
    auth = await token_for(client, "editor@example.com")

    report = await write(client, auth)

    assert report["slug"] == "chas-pechati"


async def test_two_reports_with_one_title_get_separate_slugs(client: AsyncClient) -> None:
    auth = await token_for(client, "editor@example.com")

    first = await write(client, auth)
    second = await write(client, auth)

    assert first["slug"] != second["slug"]


async def test_reading_time_is_computed_not_declared(client: AsyncClient) -> None:
    """An author who edits a report cannot leave a stale length behind."""
    auth = await token_for(client, "editor@example.com")

    short = await write(client, auth)
    # Several paragraphs rather than one enormous one: a single block is capped at
    # 4 000 characters, which is the schema doing its job.
    long_body = [{"kind": "paragraph", "text": "слово " * 600} for _ in range(4)]
    longer = await write(client, auth, title="Длинный", blocks=long_body)

    # Never zero, however short — «0 МИН ЧТЕНИЯ» is not a thing a reader believes.
    assert short["read_minutes"] >= 1
    assert longer["read_minutes"] > short["read_minutes"]


async def test_contents_come_from_the_headings_themselves(client: AsyncClient) -> None:
    """So a contents list can never disagree with the article it describes."""
    auth = await token_for(client, "editor@example.com")

    report = await write(
        client,
        auth,
        blocks=[
            {"kind": "heading", "text": "Первый раздел"},
            {"kind": "paragraph", "text": "Текст."},
            {"kind": "heading", "text": "Второй раздел"},
        ],
    )

    assert [entry["text"] for entry in report["toc"]] == ["Первый раздел", "Второй раздел"]
    # Position-suffixed, so two sections sharing a title still scroll to the right
    # one rather than both landing on the first.
    assert report["toc"][0]["anchor"] != report["toc"][1]["anchor"]


async def test_a_malformed_block_is_refused_at_the_edge(client: AsyncClient) -> None:
    """A code listing with no code would render as an empty grey box."""
    auth = await token_for(client, "editor@example.com")

    response = await client.post(
        "/journal",
        json=a_report(blocks=[{"kind": "code", "label": "ENGINE.PY"}]),
        headers=auth,
    )

    assert response.status_code == 422


# --------------------------------------------------------------- visibility


async def test_a_draft_is_invisible_to_the_public(client: AsyncClient) -> None:
    auth = await token_for(client, "editor@example.com")
    draft = await write(client, auth)

    assert (await client.get("/journal")).json()["rows"] == []
    # 404, not 403: a 403 confirms the slug exists.
    assert (await client.get(f"/journal/{draft['slug']}")).status_code == 404


async def test_an_editor_sees_their_drafts_beside_the_published_ones(
    client: AsyncClient,
) -> None:
    """A draft is only useful if you can see it next to what it will sit beside."""
    auth = await token_for(client, "editor@example.com")
    await write(client, auth)

    assert len((await client.get("/journal", headers=auth)).json()["rows"]) == 1


async def test_publishing_makes_it_public(client: AsyncClient) -> None:
    auth = await token_for(client, "editor@example.com")
    draft = await write(client, auth)

    await client.patch(f"/journal/{draft['slug']}", json={"is_published": True}, headers=auth)

    body = (await client.get(f"/journal/{draft['slug']}")).json()
    assert body["is_published"] is True
    assert body["published_at"] is not None


async def test_republishing_keeps_the_original_date(client: AsyncClient) -> None:
    """A report pulled down and put back is not new, and must not jump the index."""
    auth = await token_for(client, "editor@example.com")
    report = await write(client, auth, is_published=True)
    first_date = report["published_at"]

    await client.patch(f"/journal/{report['slug']}", json={"is_published": False}, headers=auth)
    restored = await client.patch(
        f"/journal/{report['slug']}", json={"is_published": True}, headers=auth
    )

    assert restored.json()["published_at"] == first_date


# ---------------------------------------------------------------- the gate


async def test_a_customer_cannot_write(client: AsyncClient) -> None:
    auth = await token_for(client, "reader@example.com")

    assert (await client.post("/journal", json=a_report(), headers=auth)).status_code == 403


async def test_a_stranger_cannot_write(client: AsyncClient) -> None:
    assert (await client.post("/journal", json=a_report())).status_code == 401


async def test_a_customer_cannot_edit_or_delete(client: AsyncClient) -> None:
    editor = await token_for(client, "editor@example.com")
    report = await write(client, editor, is_published=True)
    reader = await token_for(client, "reader@example.com")

    assert (
        await client.patch(f"/journal/{report['slug']}", json={"title": "Нет"}, headers=reader)
    ).status_code == 403
    assert (await client.delete(f"/journal/{report['slug']}", headers=reader)).status_code == 403


# ----------------------------------------------------------------- the index


async def test_the_chips_count_the_journal_not_the_page(client: AsyncClient) -> None:
    """A chip that counted the page would tell a reader the farm wrote three reports."""
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, title="А", section="cost", is_published=True)
    await write(client, auth, title="Б", section="cost", is_published=True)
    await write(client, auth, title="В", section="fleet", is_published=True)

    body = (await client.get("/journal", params={"limit": 1})).json()

    assert len(body["rows"]) == 1
    assert body["total"] == 3
    assert {entry["section"]: entry["count"] for entry in body["counts"]} == {
        "cost": 2,
        "fleet": 1,
    }


async def test_filtering_by_section_keeps_the_other_chips_countable(
    client: AsyncClient,
) -> None:
    """Or picking a section would empty the row that lets you pick a different one."""
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, title="А", section="cost", is_published=True)
    await write(client, auth, title="Б", section="fleet", is_published=True)

    body = (await client.get("/journal", params={"section": "cost"})).json()

    assert [row["title"] for row in body["rows"]] == ["А"]
    assert {entry["section"] for entry in body["counts"]} == {"cost", "fleet"}


async def test_search_finds_a_russian_title_whatever_the_case(client: AsyncClient) -> None:
    """SQL `lower()` is ASCII-only in places, which is why the folded column exists."""
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, title="Себестоимость Часа", is_published=True)

    found = (await client.get("/journal", params={"q": "СЕБЕСТОИМОСТЬ"})).json()

    assert len(found["rows"]) == 1


async def test_the_newest_report_is_the_featured_one(client: AsyncClient) -> None:
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, title="Старый", is_published=True)
    await write(client, auth, title="Новый", is_published=True)

    assert (await client.get("/journal/latest")).json()["title"] == "Новый"


async def test_an_empty_journal_has_no_featured_report(client: AsyncClient) -> None:
    """`null`, so the screen omits the lead block rather than framing nothing."""
    assert (await client.get("/journal/latest")).json() is None


async def test_an_article_offers_its_neighbours(client: AsyncClient) -> None:
    auth = await token_for(client, "editor@example.com")
    for title in ("А", "Б", "В", "Г", "Д"):
        await write(client, auth, title=title, is_published=True)

    body = (await client.get("/journal/d")).json()

    assert len(body["neighbours"]) == 3
    assert all(entry["slug"] != body["slug"] for entry in body["neighbours"])


# --------------------------------------------------------------- the cadence


async def test_the_cadence_is_measured_from_real_gaps(
    client: AsyncClient, settings: Settings
) -> None:
    """«ВЫХОДИТ 1 / НЕД» is a claim about the farm's habits, so it is counted.

    Three reports a week apart is one a week, and the index says so without
    anybody typing the number in.
    """
    auth = await token_for(client, "editor@example.com")
    for offset, title in enumerate(("А", "Б", "В")):
        await write(client, auth, title=title, is_published=True)
        await backdate(settings, title, weeks=2 - offset)

    assert Decimal((await client.get("/journal")).json()["weekly_rate"]) == Decimal("1.0")


async def test_a_journal_with_no_rhythm_reports_none(client: AsyncClient) -> None:
    """A batch published in one go has no cadence — «0 / НЕД» would read as stopped."""
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, title="А", is_published=True)
    await write(client, auth, title="Б", is_published=True)

    assert (await client.get("/journal")).json()["weekly_rate"] is None


async def test_one_report_is_not_a_cadence(client: AsyncClient) -> None:
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, is_published=True)

    assert (await client.get("/journal")).json()["weekly_rate"] is None


# ------------------------------------------------------- drafts and the lead


async def test_a_draft_never_becomes_the_featured_report(client: AsyncClient) -> None:
    """Not even for the person writing it.

    The lead block bills this as «ГЛАВНЫЙ МАТЕРИАЛ ВЫПУСКА» — a claim about what
    the farm has put out. An editor seeing their draft in the grid is useful,
    because it sits where it will sit; seeing it presented as the issue's lead
    article is wrong, and wrong in a way that is easy to publish by mistake.
    """
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, title="Опубликованный", is_published=True)
    await write(client, auth, title="Черновик")

    for headers in ({}, auth):
        featured = (await client.get("/journal/latest", headers=headers)).json()
        assert featured["title"] == "Опубликованный", headers


async def test_the_lead_counts_issues_and_the_chip_counts_rows(
    client: AsyncClient,
) -> None:
    """Two different questions, two different numbers, both true.

    «N ВЫПУСКОВ» is what the farm published. «ВСЕ N» is what the filter will show
    the person looking — which for an editor includes their own drafts, or they
    could not filter to them.
    """
    auth = await token_for(client, "editor@example.com")
    await write(client, auth, title="Вышел", is_published=True)
    await write(client, auth, title="Ещё пишется")

    editor_view = (await client.get("/journal", headers=auth)).json()
    assert editor_view["total"] == 2
    assert editor_view["published_total"] == 1

    public_view = (await client.get("/journal")).json()
    assert public_view["total"] == public_view["published_total"] == 1
