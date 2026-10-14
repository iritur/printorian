"""Whether the session cookie should carry `Secure`, decided in one place.

It used to be `request.url.scheme == "https"` at each call site, and behind Caddy
that is always false: Caddy terminates the connection and talks to the API over
plain HTTP, and uvicorn ignores `X-Forwarded-Proto` from any address it was not
told to trust — `compose.prod.yml` tells it none. So the flag was never set, on
any deployment. Harmless on today's plain-HTTP LAN; wrong the day the edge
terminates TLS (ADR-0016 Stage 3), when a session token would ride every
plain-HTTP request a browser could be talked into making (security review, #27).

**`X-Forwarded-Proto` is read directly, and trusting it costs nothing here.**
Both Caddyfiles set it from the connection Caddy actually received, and the
`api` service publishes no host port, so nothing else can reach the API to set
it. A forged header could at worst mark *the forger's own* cookie `Secure` over
plain HTTP, and the forger's browser would then not send it back — there is no
one else to harm. Teaching uvicorn to trust the proxy instead would have
rewritten `request.client` too, which `throttle_key` and the webhook source check
read deliberately and differently; this keeps the change to the one flag.

Sign-out and the password change delete the cookie with the same flag. A browser
refuses to let a non-`Secure` `Set-Cookie` overwrite a `Secure` one, so a delete
that disagreed with the set would leave the old token in place.
"""

from __future__ import annotations

from starlette.requests import Request


def cookie_is_secure(request: Request) -> bool:
    """True when the browser reached us over HTTPS, directly or through the proxy."""
    if request.url.scheme == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "")
    # A proxy chain lists one protocol per hop; the first is the browser's.
    return forwarded.split(",")[0].strip().lower() == "https"


__all__ = ["cookie_is_secure"]
