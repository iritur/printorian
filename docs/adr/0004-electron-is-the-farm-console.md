# ADR-0004 — Electron is the farm console, not a second admin app

**Status:** Superseded by [ADR-0016](0016-two-web-apps-no-desktop.md) · Phase 0 · 2026-08-05

> Kept for the reasoning, not the decision. Building this ADR out is what showed
> its own premise to be thinner than it looked: of the six capabilities it claimed
> only a desktop could provide, one turned out to be true. The notes below on
> cross-origin auth, token storage and slicer containment are the evidence
> ADR-0016 was decided on, and the parsing rules survive it.

## Context
A desktop app and a web app with overlapping features are two implementations of the same
screens. Whatever they share in principle, they diverge in practice, because nothing
forces a change to land in both.

## Decision
The Electron app does only what a browser cannot: LAN printer discovery, NAS/local model
library access and folder watching, launching the external slicer for the prep queue,
kiosk/wall mode, native notifications, and barcode/QR scanner input. Everything else in
the desktop is the same React components from `packages/ui` pointed at the same API.
The web app owns the storefront and light admin.

## Consequences
* Minimal duplicated UI surface.
* A shared component library and generated API client are prerequisites, not nice-to-haves.
* Electron security posture is fixed: `contextIsolation: true`, `nodeIntegration: false`,
  privileged operations behind a narrow typed preload bridge.

## Implementation notes (Phase 3)

Building the console surfaced three things the original decision implied but did
not state.

**The console is never same-origin with the API**, so it cannot use the session
cookie the storefront relies on. It authenticates with a bearer token, which has
consequences in three places:

* CORS is now configurable (`PRINTORIAN_CORS_ORIGINS`), empty by default so the
  same-origin storefront is unaffected and the API does not advertise itself
  otherwise. `allow_credentials` stays **false**: the console sends a token, so
  the browser never needs to attach a cookie cross-site, and a listed origin
  cannot ride on someone's session.
* `ApiClient` sends cookies only when it has no token, and takes an explicit
  `credentials` option for the sign-in call that has no token yet. Asking for
  cookies obliges the server to answer `Access-Control-Allow-Credentials: true`,
  which is exactly what the API declines to do.
* **No WebSocket client can set an `Authorization` header.** The token travels as
  a `Sec-WebSocket-Protocol` value (`bearer.<token>`) rather than a query
  parameter, which would be recorded by every proxy and access log in between.
  The negotiated subprotocol echoes back `printorian.v1` only, never the token.

**The token is persisted, encrypted by the OS keychain** via Electron's
`safeStorage`, in the main process. A wall display has to come back after a power
cut showing the farm rather than a login form nobody is standing next to. It
deliberately does not fall back to plain text when the keychain is unavailable —
signing in each morning is a nuisance, a readable token on a shop-floor machine
is a credential leak.

**Alerting is a short list, on purpose.** Unreachable, finished, failed — nothing
else, and repeats of the same condition are suppressed for five minutes. A
console that notifies on everything trains the floor to ignore it, which is worse
than one that notifies on nothing.


## Slicer integration (Phase 4)

The prep queue's one genuinely native piece: opening the engineer's slicer and
noticing what it exports. Three rules make the surface safe enough to keep.

**The renderer never names an executable.** `slicer.launch()` takes a model, not a
program. Which program opens is the stored setting a human chose through a native
dialog. A path arriving from page content — an injected string, a compromised API
response — would otherwise become arbitrary code execution inside a privileged
shell, and that is the whole risk of putting `spawn` behind a bridge at all.

**The model is staged where the app controls**, under `userData`, with `basename`
stripping any directory the renderer put in the name. **Only the configured export
folder is read**, resolved first so `..` cannot climb out and a sibling folder
whose name merely starts the same (`/exports-private` beside `/exports`) does not
pass a naive prefix check.

**Parsed numbers are never guessed.** Print minutes and per-slot grams become the
truth an order is repriced against (ADR-0013), so the parser returns `null` rather
than an estimate and `parsed` is true only when *both* were found — half-filled
forms are the ones that get accepted without reading. Bambu's `model printing
time` is preferred over `total estimated time`, which includes preheating and
would make every job look longer than the bed is busy.

**Blocked on model storage.** The console can open a slicer, but the farm does not
keep customer models: the pricing endpoint reads an uploaded STL to analyse it and
discards the bytes, and there is no `ModelAsset` entity. Until that exists there is
nothing to hand a slicer, and the prep screen says exactly that rather than
reporting a transient failure. Model storage is the prerequisite for the rest of
Phase 4's prep flow.
