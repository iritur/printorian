"""Where a request came from — one answer, for every reader that needs one.

Security review F3, decided by the owner as §5(a): **trust one named proxy.**
There used to be two answers, read from opposite ends of `X-Forwarded-For`: the
first entry for the security screen and the YooKassa webhook's source check, the
last for the rate ceilings and the sign-in lockout. They agreed only because
Caddy strips a caller's header and writes exactly one entry — a proxy behaviour
nothing stated, and the day the API was reached any other way, the two halves
were wrong in different directions (a forged first entry passed the webhook's
allow-list; a forged last entry would have bought allowance).

Now there is a rule, and it is stated in configuration rather than inferred:

* if the **socket peer is a trusted proxy** (`Settings.trusted_proxies`), the
  request is from the address that proxy appended — the **last** entry, the
  only one in the chain written by something the farm runs;
* otherwise the request is from **its socket peer**, and any `X-Forwarded-For`
  it carries is the caller's own claim and is ignored.

So a forged header buys nothing whichever way the API is reached: through Caddy
the forgery is in front of the entry Caddy wrote, and around Caddy the header is
not read at all. Putting a second proxy in front of Caddy — a load balancer at
the Stage 3 edge — would make Caddy append *that* proxy's address; the answer
then is a hop count, and this function is the one line to change.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from fastapi import Request

#: `sessions.client_ip` is a `String(45)` — the longest IPv6 text form.
_MAX = 45


@lru_cache(maxsize=16)
def _networks(
    trusted: tuple[str, ...],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return tuple(ipaddress.ip_network(entry, strict=False) for entry in trusted)


def is_trusted_proxy(peer: str, trusted: list[str]) -> bool:
    """Whether ``peer`` is one of the proxies whose forwarding header is believed."""
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        # Not an address at all — a test transport's name, a Unix socket. Nothing
        # that cannot be placed on the network can be a proxy the farm runs.
        return False
    return any(address in network for network in _networks(tuple(trusted)))


def client_address(request: Request) -> str:
    """The address this request came from, by the rule in the module docstring."""
    peer = request.client.host if request.client else ""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded and is_trusted_proxy(peer, request.app.state.settings.trusted_proxies):
        return forwarded.rsplit(",", 1)[-1].strip()[:_MAX]
    return peer[:_MAX]


__all__ = ["client_address", "is_trusted_proxy"]
