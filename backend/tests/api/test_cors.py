"""Cross-origin access.

The storefront is same-origin behind the reverse proxy (ADR-0003) and needs none
of this. The desktop console is never same-origin, so a deployment that runs one
lists its origin explicitly — and the API stays shut to everyone else.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from printorian.api.app import create_app
from printorian.core.clock import FixedClock
from printorian.core.config import Settings
from printorian.core.events import EventBus


async def test_no_cross_origin_access_by_default(
    settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """The storefront is same-origin behind the reverse proxy (ADR-0003), so the
    API does not advertise itself to other origins unless a deployment says so."""
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        response = await http.get("/health", headers={"Origin": "http://console.local"})

    assert "access-control-allow-origin" not in response.headers


async def test_a_listed_origin_is_allowed(
    settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """What the desktop console needs: it is never same-origin with the API."""
    allowed = settings.model_copy(update={"cors_origins": "http://console.local"})
    app = create_app(allowed)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        response = await http.get("/health", headers={"Origin": "http://console.local"})

    assert response.headers["access-control-allow-origin"] == "http://console.local"


async def test_an_unlisted_origin_is_still_refused(
    settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    allowed = settings.model_copy(update={"cors_origins": "http://console.local"})
    app = create_app(allowed)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        response = await http.get("/health", headers={"Origin": "http://evil.example"})

    assert response.headers.get("access-control-allow-origin") != "http://evil.example"


async def test_cookies_never_travel_cross_origin(
    settings: Settings, clock: FixedClock, bus: EventBus
) -> None:
    """The console authenticates with a bearer token. Allowing credentials would
    let a listed origin ride on someone's session cookie for no benefit."""
    allowed = settings.model_copy(update={"cors_origins": "http://console.local"})
    app = create_app(allowed)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        response = await http.get("/health", headers={"Origin": "http://console.local"})

    assert "access-control-allow-credentials" not in response.headers
