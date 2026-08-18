"""The journal as RSS.

**This is the one place the backend legitimately emits prose.** ADR-0012 says
codes cross the wire and the client picks the wording, and that holds because
there is always a client to pick it. A feed has no client of ours: the document
*is* the rendering, read by somebody else's reader, so the words have to be in it.
Rendering it anywhere but here would mean the storefront generating XML, which is
worse.

RSS 2.0 rather than Atom. Both work; RSS is what the readers a Russian audience
actually uses default to, and the kit's button says «Подписаться на RSS».

Every URL is absolute and derived from the request, so the same code serves the
farm's LAN, a tunnel and a production domain without a configured base URL to
forget to change.
"""

from __future__ import annotations

from datetime import datetime
from xml.sax.saxutils import escape

from printorian.contexts.journal import PostCard

TITLE = "Printorian · Журнал"
DESCRIPTION = (
    "Как устроена автоматическая ферма 3D-печати изнутри: расчёты, решения "
    "и ошибки, включая те, которые пришлось откатывать."
)
LANGUAGE = "ru"

#: RFC 822, which is what RSS dates are. `strftime('%a, %d %b %Y')` would render
#: the weekday and month in the server's locale, and a Russian «Вт, 18 Авг» is not
#: a date any reader can parse.
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def rfc822(moment: datetime) -> str:
    """A date an RSS reader will accept, in English regardless of the server."""
    return (
        f"{_DAYS[moment.weekday()]}, {moment.day:02d} {_MONTHS[moment.month - 1]} "
        f"{moment.year} {moment:%H:%M:%S} +0000"
    )


def feed(rows: list[PostCard], *, site: str, built_at: datetime) -> str:
    """Render published reports as an RSS channel.

    ``site`` is the storefront's root with no trailing slash — the place a reader
    clicking an item should land, which is *not* where this XML is served from.
    """
    items = "".join(_item(row, site=site) for row in rows)
    newest = next((row.published_at for row in rows if row.published_at), built_at)

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{escape(TITLE)}</title>\n"
        f"    <link>{escape(site)}/journal</link>\n"
        f"    <description>{escape(DESCRIPTION)}</description>\n"
        f"    <language>{LANGUAGE}</language>\n"
        f"    <lastBuildDate>{rfc822(newest)}</lastBuildDate>\n"
        # `atom:link rel="self"` is how a reader learns the feed's own address
        # after somebody has copied the file around. Every validator asks for it.
        f'    <atom:link href="{escape(site)}/api/journal/rss" rel="self" '
        'type="application/rss+xml"/>\n'
        f"{items}"
        "  </channel>\n"
        "</rss>\n"
    )


def _item(row: PostCard, *, site: str) -> str:
    """One report.

    The link is the storefront's article URL, not this API's JSON — a reader
    clicking through wants the page, and pointing them at a payload would make the
    feed technically valid and practically useless.

    `guid` is that same URL and `isPermaLink="true"`, which is what stops a reader
    showing a report twice after an edit: the identity is the address, and the
    address does not change when the text does.
    """
    link = f"{site}/journal/{row.slug}"
    dated = f"    <pubDate>{rfc822(row.published_at)}</pubDate>\n" if row.published_at else ""
    return (
        "    <item>\n"
        f"      <title>{escape(row.title)}</title>\n"
        f"      <link>{escape(link)}</link>\n"
        f'      <guid isPermaLink="true">{escape(link)}</guid>\n'
        f"  {dated}"
        f"      <description>{escape(row.excerpt)}</description>\n"
        "    </item>\n"
    )
