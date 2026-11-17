---
name: printorian-silent-printer-60s
description: "A silent (unreachable) Bambu printer costs a measured 60.1 s per connect attempt — hence the shared driver pool with CONNECT_CONCURRENCY=8 and retry backoff 30 s doubling to 5 min with jitter; connection waits happen in a thread, never on the event loop."
metadata:
  node_type: memory
  type: project
---

**Why:** Without the pool, one dead printer on the LAN stalls the telemetry
sweep for everyone — the 60.1 s TCP timeout is paid serially per printer,
per 5-second sweep, which is arithmetic that ends the sweep's purpose.

**How to apply:** Do not "simplify" the driver pool away or inline connects
into async code. New drivers join the pool; connection attempts go through
it (thread + `CONNECT_CONCURRENCY` = 8); retries use 30 s doubling to 5 min
with jitter. A printer silent past the backoff ceiling is `Offline` plus an
attention event (ADR-0007) — never silently dropped from the roster.

Evidence: docs/DEVELOPMENT.md (Workers section: the five loops, the shared
driver pool, "measured silent-printer behavior (60.1 s per silent printer
fixed; retry 30 s doubling to 5 min with jitter)").

Related: [[printorian-bambu-ftps-session-reuse]].
