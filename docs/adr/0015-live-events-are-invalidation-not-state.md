# ADR-0015 — Live events invalidate the client's view; they do not carry it

**Status:** Accepted · Phase 3 · 2026-08-08

## Context
The shop floor needs the fleet table to change when the farm changes: a print
finishing, a machine dropping off the network, a service falling due. Phase 3 added
the WebSocket fan-out (`api/ws.py`) that pushes every fleet, order and payment event
to entitled clients.

The obvious next step is to let the client apply those events directly to the rows it
is holding — a `fleet.printer_state_changed` sets `row.state` and the table updates
without a request. That is where two problems start.

**Events are not complete rows.** `PrinterStateChanged` carries `from_state` and
`to_state` and nothing else. It does not carry the progress percentage, the ETA, the
last-seen time, or whether the machine now needs attention. A client that patched
only `state` would leave the other cells showing values from before the change — a
row that is half new and half old, which is worse than either.

**Completing the row means reimplementing the domain.** The missing fields are
derived: `needs_attention` is a policy over state, maintenance intervals and printed
hours; `eta` is only meaningful while printing. Computing them in TypeScript means
two implementations of fleet policy in two languages, and the day they disagree the
screen is confidently wrong. A UI that computes what it could have asked for is
inventing plausible values for a machine it cannot reach — the same defect ADR-0007
forbids in the drivers, with better manners.

**A dropped socket loses events outright.** The bus is in-process and has no replay.
Every event published while a client was reconnecting is simply gone. A client that
resumed patching on reconnect would carry that hole forever, silently.

## Decision

**An event means "you are out of date". It does not mean "here is the new truth".**

* The client treats any relevant event as an **invalidation signal** and refetches the
  affected collection over HTTP. The server remains the single place where a row's
  shape and its derived fields are decided.
* Bursts are **coalesced** (`COALESCE_MS`, 250 ms). A farm restarting emits a dozen
  events in a second; that is one refetch, not a dozen.
* `EventStream` calls `onResync` on **every successful connection, including the
  first**. A reconnect and a cold start have the same problem — the client knows
  nothing trustworthy — so they take the same path. This is what makes the gap
  harmless: events missed while disconnected do not need replay, because the resync
  supersedes them.
* Connection status is **part of the UI contract**, not an internal detail.
  `StreamStatus` is surfaced, and the fleet screen states plainly when what is on
  screen may be stale rather than showing the last known values as though they were
  current.
* A refused handshake (close code 4401) is **not retried**. Retrying a refusal
  hammers the API and can never succeed; the caller signs in again, which remounts
  the stream.

## Consequences

* Live updates cost one small HTTP request per change-burst rather than zero. On a
  farm-sized fleet this is negligible, and it buys a client that cannot drift.
* The frontend holds no fleet policy. `packages/events` knows event *names* and
  envelope shape; it does not know what any of them mean.
* Correctness does not depend on delivery guarantees. The stream may drop, duplicate
  or reorder events and the view still converges, because every connection resyncs.
  This is what lets the in-process bus stay as simple as it is (ARCHITECTURE §8).
* An event name added on the server that this build has never heard of arrives as a
  weakly-typed envelope and is ignored, rather than breaking the stream.

## Alternatives rejected

* **Patch rows from event payloads** — needs either complete rows on the wire or
  domain logic in the client. See Context.
* **Fatten the events into full row snapshots** — makes every event carry a view
  model, couples the bus to the table's current columns, and still leaves the gap
  problem unsolved for clients that were disconnected.
* **Poll on a timer and drop the socket** — the scenario asks for the floor to see a
  finished print promptly; a poll interval short enough to feel live is most of the
  request volume of polling with none of the latency benefit.
* **Event log with replay-since-cursor** — solves the gap properly and is the right
  answer if offline clients ever need exact history. It is a persisted log, a
  retention policy and a cursor protocol; a refetch is one request. Revisit if the
  Electron console needs to reconstruct what happened while a shop-floor machine was
  asleep.
