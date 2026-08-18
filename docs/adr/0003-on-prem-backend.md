# ADR-0003 — Backend runs on-premises on the farm LAN

**Status:** Accepted · Phase 0 · 2026-08-05

## Context
Printer control needs LAN access: Bambu LAN mode is MQTT over TLS to the printer's own
address, plus FTPS for plate upload. A cloud backend would need an on-prem agent and a
distributed-system layer.

## Decision
One on-prem server on the farm LAN runs the API, workers, PostgreSQL and Redis. The public
storefront is exposed through a reverse proxy.

## Consequences
* Lowest latency to printers; no split-brain; no agent protocol to design.
* The server is a single point of failure - mitigated by tested restore (Phase 7), UPS, and
  a degraded mode where printers keep printing and reconcile on reconnect.
* Multi-site is out of scope; revisit with a new ADR if a second location opens.

## Clarification — what "exposed through a reverse proxy" means (Phase 4, 2026-08-10)

[ADR-0016](0016-two-web-apps-no-desktop.md) splits the frontend in two and puts the
storefront on internet hosting. That does **not** move the backend.

The internet host serves the storefront's static bundle and reverse-proxies `/api`
back to the farm through a tunnel. It holds no data at rest: customer records,
orders and finances stay on the premises, and the rented machine is a bundle and a
proxy. The console is served by the on-prem server directly, on the LAN.

The consequence worth naming plainly: **the storefront is unreachable while the
farm's uplink is down.** Orders already placed are unaffected and printers keep
printing, but the shop cannot take new ones. That was chosen over the alternative
— backend on rented hardware plus an on-prem agent for the printers — because the
alternative is precisely the distributed-system layer this ADR exists to avoid,
and it puts the farm's finances on someone else's machine to buy uptime for a shop
that cannot fulfil anything while the farm is offline anyway.
