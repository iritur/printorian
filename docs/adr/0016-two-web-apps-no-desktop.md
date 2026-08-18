# ADR-0016 — Two web apps, no desktop app

**Status:** Accepted · Phase 4 · 2026-08-10
**Supersedes:** [ADR-0004](0004-electron-is-the-farm-console.md)

## Context

ADR-0004 kept an Electron console for "only what a browser cannot": LAN discovery,
NAS library access, launching the slicer, folder watching, kiosk mode, native
notifications, scanner input.

Building it showed the list was shorter than it looked.

* **Kiosk mode** is `chrome --kiosk`. Electron added a packaging step to reach a
  flag that already exists.
* **Native notifications** are the Web Notifications API. The alerting policy that
  actually mattered — three conditions, repeats suppressed for five minutes — is
  policy, not platform.
* **Scanner input** is keyboard input; HID scanners type. Camera scanning is
  `getUserMedia`.
* **LAN discovery** and **NAS access** were never built, and belong on the server
  regardless: the backend already sits on the farm LAN (ADR-0003), so discovering
  printers from a client was solving the problem from the wrong side of the wire.

Meanwhile Electron *cost* more than it looked. Because the console is never
same-origin with the API, it could not use the storefront's session cookie, and
that single fact produced configurable CORS, a bearer-token path through
`ApiClient`, a `Sec-WebSocket-Protocol` scheme for WebSocket auth, and OS-keychain
token persistence with a deliberate refusal to fall back to plaintext. All of it
correct, none of it wanted.

That leaves exactly one genuinely native capability: **launching the engineer's
slicer and watching its export folder.** One capability is not a desktop app.

## Decision

No desktop app. Two web frontends against one backend and one database:

| App | Served from | Audience |
|---|---|---|
| **Storefront** (`apps/web`) | Internet hosting | Customers: catalogue, configurator, checkout, cabinet, journal |
| **Console** (`apps/console`) | The farm server, on the LAN | Staff: fleet, materials, order desk, prep, users, finances, analytics |

The backend and PostgreSQL stay on-premises, unchanged from ADR-0003. The internet
host serves the storefront's static bundle and reverse-proxies `/api` back to the
farm over a tunnel, so **both apps remain same-origin with their API** and both use
the session cookie.

Slicing becomes a manual round-trip — see
[ADR-0006](0006-human-gated-slicing.md), amended.

## Consequences

* **The cross-origin machinery becomes optional rather than load-bearing.** CORS
  stays configurable and `ApiClient` keeps its bearer-token path, because a future
  caller may need them; nothing in the product depends on them any more.
* **Deployment gains a tunnel and a dependency.** The storefront is unreachable
  while the farm's uplink is down. Accepted deliberately: printers keep printing,
  orders already taken are unaffected, and the alternative — an on-prem agent plus
  a distributed data layer — is what ADR-0003 rejected on the same evidence.
* **The farm keeps working when the internet does not.** The console is on the
  LAN with the database and the printers.
* **Finances and customer data never leave the premises.** The internet host holds
  a static bundle and a proxy, no data at rest.
* **One less toolchain**: no Electron build, no packaging, no `safeStorage`, no
  preload bridge to keep narrow, no second update channel.
* The wall display is a browser in kiosk mode pointed at a console route, and it
  signs in with the same cookie as everyone else.

## What was kept

The plate parser survives the deletion, ported to the backend: print minutes and
per-slot grams read from an uploaded plate, preferring Bambu's `model printing
time` over `total estimated time` (which includes preheating), and returning
nothing rather than an estimate when either is absent. Those numbers are what an
order is repriced against under ADR-0013, so guessing is not an option — the
reason is the same whether the parsing happens on a desktop or a server.
