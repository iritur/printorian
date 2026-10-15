"""The API trusts exactly the proxies the farm runs, by the addresses they hold.

Security review §5(a): the API believes `X-Forwarded-For` only from a peer named
in `trusted_proxies` (`api/client_address.py`). In `deploy/compose.prod.yml` that
list is written beside the API, and the addresses it names are written on two
other services, and the subnet they sit in on a third block. Three places that
must agree, and the way they would stop agreeing is half an edit:

* a proxy's address changed and the list not — the API stops believing its own
  proxy, every customer shares one rate ceiling, and every YooKassa notification
  is refused as coming from Caddy;
* the list widened to a network, or to an address nothing holds — the API
  believes a header from something that is not the farm's proxy.

So this reads the file as YAML and holds the three together, and parses the
environment value the way the API will, through `Settings`.
"""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from printorian.core.config import Settings

COMPOSE = Path(__file__).resolve().parents[3] / "deploy" / "compose.prod.yml"
PROXIES = ("console", "storefront")


def _compose() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return loaded


def _trusted() -> list[str]:
    raw = _compose()["services"]["api"]["environment"]["PRINTORIAN_TRUSTED_PROXIES"]
    parsed: list[str] = json.loads(raw)
    return parsed


def test_the_api_trusts_exactly_the_two_proxies_by_their_fixed_addresses() -> None:
    services = _compose()["services"]
    held = {services[name]["networks"]["default"]["ipv4_address"] for name in PROXIES}

    assert set(_trusted()) == held
    # Single addresses, never a network: "trust one named proxy".
    for entry in _trusted():
        assert ipaddress.ip_network(entry).num_addresses == 1, entry


def test_the_fixed_addresses_are_inside_the_farms_own_subnet() -> None:
    [pool] = _compose()["networks"]["default"]["ipam"]["config"]
    subnet = ipaddress.ip_network(pool["subnet"])

    for entry in _trusted():
        assert ipaddress.ip_address(entry) in subnet, entry


def test_the_value_parses_the_way_the_api_will_read_it(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _compose()["services"]["api"]["environment"]["PRINTORIAN_TRUSTED_PROXIES"]
    monkeypatch.setenv("PRINTORIAN_TRUSTED_PROXIES", raw)

    assert Settings().trusted_proxies == _trusted()
