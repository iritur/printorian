"""Three small guards the security review's second pass recorded (#27), as pure rules.

* **A WebSocket handshake from another site is refused** before its cookie is read.
* **The journal's unsubscribe token never reaches the access log.**
* **The session cookie is `Secure` whenever the browser arrived over HTTPS**, which
  behind Caddy is only visible in `X-Forwarded-Proto`.

The edge halves — the socket really refusing, the cookie really carrying the flag —
are in `tests/api/test_events_ws.py` and `tests/api/test_guards_api.py`.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from printorian.api.middleware import loggable_path
from printorian.api.session_cookie import cookie_is_secure
from printorian.api.ws import origin_allowed


@pytest.mark.parametrize(
    ("origin", "host", "allowed", "loopback", "expected"),
    [
        (None, "farm.lan", [], False, True),  # not a browser: authenticates as usual
        ("http://farm.lan", "farm.lan", [], False, True),  # same origin, through Caddy
        ("https://shop.example:8443", "shop.example:8443", [], False, True),
        ("https://evil.example", "farm.lan", [], False, False),  # the hijack
        ("http://farm.lan.evil.example", "farm.lan", [], False, False),
        ("null", "farm.lan", [], False, False),  # sandboxed frame or file: page
        ("https://admin.example", "farm.lan", ["https://admin.example"], False, True),
        # The Vite dev proxy rewrites Host but not Origin: allowed outside production…
        ("http://127.0.0.1:5174", "127.0.0.1:8000", [], True, True),
        ("http://localhost:5173", "127.0.0.1:8000", [], True, True),
        # …and only there.
        ("http://127.0.0.1:5174", "127.0.0.1:8000", [], False, False),
    ],
)
def test_the_origin_rule(
    origin: str | None, host: str, allowed: list[str], loopback: bool, expected: bool
) -> None:
    assert origin_allowed(origin, host=host, allowed=allowed, allow_loopback=loopback) is expected


def test_the_unsubscribe_token_is_redacted_and_nothing_else_is() -> None:
    assert loggable_path("/journal/unsubscribe/s3cr3t-token") == "/journal/unsubscribe/{token}"
    assert loggable_path("/journal/subscribe") == "/journal/subscribe"
    assert loggable_path("/orders/0190a3b2") == "/orders/0190a3b2"


def _request(scheme: str, forwarded: str | None) -> Request:
    headers = [] if forwarded is None else [(b"x-forwarded-proto", forwarded.encode())]
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "method": "POST",
            "path": "/auth/sign-in",
            "headers": headers,
            "server": ("farm.lan", 80),
        }
    )


@pytest.mark.parametrize(
    ("scheme", "forwarded", "expected"),
    [
        ("https", None, True),
        ("http", None, False),  # the plain-HTTP LAN console must keep its session
        ("http", "https", True),  # behind a TLS-terminating proxy
        ("http", "HTTPS, http", True),  # a chain: the first hop is the browser's
        ("http", "http", False),
    ],
)
def test_the_cookie_is_secure_exactly_when_the_browser_used_https(
    scheme: str, forwarded: str | None, expected: bool
) -> None:
    assert cookie_is_secure(_request(scheme, forwarded)) is expected
