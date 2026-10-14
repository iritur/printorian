"""Where a request came from: one answer to show a person, one to count against.

Two functions that read opposite ends of `X-Forwarded-For`, and the difference
is the point of each — the docstrings say why. Moved out of `api/deps.py`, which
is the dependency wiring, when the API-key resolution needed its room (#29):
these are pure functions of a request and depend on nothing in that module.
"""

from __future__ import annotations

from fastapi import Request


def throttle_key(request: Request) -> str:
    """The address a ceiling is counted against — the *last* forwarded hop.

    Deliberately **not** `client_ip`, and the difference is the whole point of
    having two functions.

    `X-Forwarded-For` is a list that each proxy appends to, so the *first* entry is
    whatever the caller sent and the *last* is the peer the nearest proxy actually
    saw. `client_ip` shows the first, because a security screen is telling a person
    "this is where the request said it came from" and a wrong answer there costs
    nothing. A rate limit keyed on the first entry costs everything: one forged
    header per request and the ceiling is a free-for-all, which is worse than no
    ceiling because it looks like one.

    So this reads the last hop, and falls back to the socket peer when nothing is
    forwarded at all — the dev server, and the console on the LAN.

    **This is exactly as trustworthy as the proxy in front of it**, and what that
    proxy does was checked rather than assumed — an earlier version of this
    paragraph asserted that Caddy appends, and that is only half true. Caddy's
    `reverse_proxy` sets or augments `X-Forwarded-For`, but by default it "will
    ignore their values from incoming requests, to prevent spoofing"; it appends
    to a caller's chain only where `trusted_proxies` says the caller is itself a
    proxy. Neither `deploy/console.Caddyfile` nor `deploy/storefront.Caddyfile`
    sets `trusted_proxies`, so the header arriving here has exactly **one** entry,
    written by Caddy, and first hop and last hop are the same address.

    Which means the last-hop rule is not what protects the ceiling today — Caddy's
    stripping is. This is written to survive the day that stops being true, and
    `test_guards_api.py` pins it for the day somebody reads the two functions and
    decides they ought to agree.

    Setting `trusted_proxies`, or putting a second proxy in front, turns the header
    back into a chain and would need a hop *count* rather than "the last one". Both
    Caddyfiles carry a note pointing here, because the other reader of that
    decision is the YooKassa webhook's source check — which reads the *first* hop,
    and is safe only while there is exactly one.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()[:45]
    return (request.client.host if request.client else "")[:45]


def client_ip(request: Request) -> str:
    """Where the request came from, as best the API can tell.

    Behind the reverse proxy both apps are served through (ADR-0016), the socket
    peer is always the proxy, so the first hop of `X-Forwarded-For` is the only
    thing that varies per client. It is also client-controlled and therefore not
    evidence of anything — which is exactly why it is used here and nowhere near
    an authorization decision. The security screen shows it so a person can say
    "that was not me", and a wrong address is no worse than the proxy's own on
    every row.

    One place, so that the day this becomes a trust boundary there is one line to
    change rather than a search.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]


__all__ = ["client_ip", "throttle_key"]
