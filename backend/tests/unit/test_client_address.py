"""The API's one rule for where a request came from (security review F3, §5(a)).

`api/client_address.py`. The owner chose **trust one named proxy**: a request
whose socket peer is a configured proxy is from the address that proxy appended,
the *last* entry of `X-Forwarded-For`; anything else is from its socket peer, and
its forwarding header is the caller's claim and is not read. Each case below is
one side of that sentence.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from printorian.api.client_address import client_address, is_trusted_proxy


def a_request(peer: str, forwarded: str | None, trusted: list[str]) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(trusted_proxies=trusted)))
    return Request(
        {"type": "http", "method": "GET", "headers": headers, "client": (peer, 5000), "app": app}
    )


def test_through_a_trusted_proxy_it_is_the_address_the_proxy_appended() -> None:
    # The caller wrote the first entry; the proxy wrote the last.
    request = a_request("172.29.0.10", "185.71.76.1, 203.0.113.9", ["172.29.0.10"])

    assert client_address(request) == "203.0.113.9"


def test_around_the_proxy_a_forwarding_header_is_not_read_at_all() -> None:
    """The case the old first-entry read got wrong: a forged YooKassa address."""
    request = a_request("203.0.113.66", "185.71.76.1", ["172.29.0.10"])

    assert client_address(request) == "203.0.113.66"


def test_a_trusted_proxy_with_nothing_forwarded_is_itself() -> None:
    assert client_address(a_request("127.0.0.1", None, ["127.0.0.1"])) == "127.0.0.1"


@pytest.mark.parametrize(
    ("peer", "trusted", "expected"),
    [
        ("172.29.0.11", ["172.29.0.0/24"], True),
        ("172.29.1.11", ["172.29.0.0/24"], False),
        ("::1", ["127.0.0.1", "::1"], True),
        # Not an address at all — a test transport's name. Not a proxy we run.
        ("testclient", ["127.0.0.1"], False),
        ("127.0.0.1", [], False),
    ],
)
def test_who_counts_as_a_trusted_proxy(peer: str, trusted: list[str], expected: bool) -> None:
    assert is_trusted_proxy(peer, trusted) is expected


def test_nothing_trusted_means_every_header_is_ignored() -> None:
    request = a_request("127.0.0.1", "10.0.0.7", [])

    assert client_address(request) == "127.0.0.1"
