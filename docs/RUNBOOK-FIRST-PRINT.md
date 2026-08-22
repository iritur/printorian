# Runbook — the first real print

The largest unproven assumption in this system, and the procedure for closing it.

**What has never happened:** `printorian.drivers.bambu` — the driver the farm
dispatches through — has never talked to a printer. Phase 4's exit criterion was
demonstrated with the `mock` driver, and `tools/bambu_spike.py` proved the protocol
in standalone code that imports nothing from Printorian. The product's own path
between those two has not been run.

Everything else outstanding before production is execution against a known design.
This is the one that could still tell you the design is wrong, which is why
ROADMAP's own sequencing principle says to do it before building on top.

Work down the list. **Stop at the first step that fails** and write down what it
said — a failure here is the most valuable output of the exercise, and the message
distinguishes problems that look identical from a distance.

---

## Before you start

- A Bambu printer on the same LAN, **LAN mode enabled**, with its access code to
  hand (Settings → Network on the machine).
- Filament loaded, bed clear, and somebody in the room. Step 5 prints.
- `backend/printers.local.toml` — git-ignored, the same registry the spike tools
  read. Format is in `tools/printer_registry.py`:

```toml
[printers.p1s-01]
host = "192.168.0.180"
serial = "20P6BJ632700731"
access_code = "12345678"
```

Nothing here touches the farm database. Steps 1–3 are read-only and safe on a
machine that is mid-job.

---

## 1. Is the machine reachable at all

```bash
cd backend && ./.venv/Scripts/python.exe tools/bambu_spike.py --printer p1s-01
```

The Phase 0 experiment, unchanged. It answers "does this protocol work from this
network" without involving any Printorian code, so a failure here is a *network or
credentials* problem and not a code one — which is exactly the distinction worth
having before touching anything else.

Three failures look similar and are not: TLS handshake refused (certificate or
port), MQTT `CONNACK` rejected (access code), and no route to host (network or LAN
mode off).

## 2. Does the product's driver connect

```bash
cd backend
export PRINTORIAN_HARDWARE=p1s-01
./.venv/Scripts/python.exe -m pytest tests/contract/ -m hardware -v
```

`tests/contract/test_bambu_hardware.py` — the same contract the mock driver is held
to, run against the machine. Read-only: it connects, reads capabilities, reads
telemetry twice, and checks that wrong credentials are *refused* rather than
answered with something plausible (ADR-0007, and the exact failure V1 shipped).

**If step 1 passed and this fails, that is the finding this whole runbook exists
for** — the protocol works and the driver does not, and the gap between them is a
bug in code you own rather than a fact about the hardware.

## 3. Does the farm see it

Register the printer through the console (Принтеры → Добавить), then watch the
fleet board. The telemetry poller runs every five seconds and the state should
change from `offline` within one sweep.

This is the first step that involves the database, the fleet context and the event
relay together. What it proves beyond step 2 is that credentials survive
encryption at rest (ADR-0014) and that a state change reaches a screen.

## 4. Prepare a plate

Slice something small — a calibration cube is ideal — in Bambu Studio, for *this
machine and this filament*, and take it through the farm's own prep chain rather
than uploading it by hand: place an order, let it reach the prep queue, and attach
the plate there.

The reason to do it this way is that dispatch reads `print_minutes` and
`filament_grams` **out of the 3MF** (ADR-0013 — numbers are parsed, never asked
for). A hand-placed file skips the parsing and proves less than it appears to.

## 5. Let it dispatch

Do not press anything. Pay the order and watch.

The scheduler ticks every thirty seconds and should walk the job itself:

```
pending → ready → assigned → dispatching → printing
```

with an `AssignmentRecord` naming every machine it considered and why the others
were rejected. **That, with a real printer at the end of it, is Phase 4's exit
criterion actually met** rather than demonstrated against a mock.

If it stalls, the state it stalled in says where: `ready` means the scheduler did
not pick it (capability, material, colour — read the assignment record),
`assigned` means dispatch did not start, `dispatching` means the upload or the
start command failed.

## 6. Write down what you learned

- Update `docs/DESIGN-KIT.md` and `HANDOFF.md`: "never run against real hardware"
  stops being true, and it is currently stated in several places.
- Anything the machine did that the driver did not expect belongs in
  `docs/BAMBU-LAN-PROTOCOL.md` beside the rest of the spike's findings, and
  anything it *reported* differently belongs as a fixture in
  `tests/contract/test_bambu_report.py` — that file's existing fixture is a real
  payload for exactly this reason, and a second machine is a second data point.
- If dispatch failed in a way the code cannot fix, ROADMAP names the fallback:
  auto-dispatch becomes auto-assign plus operator confirmation. That is a scope
  change, and finding it here is the cheapest place to find it.

---

## What this does not cover

The upload-and-start half of `tests/contract/test_bambu_hardware.py` is written as
a skip, not as a test: it needs a known-good 3MF sliced for the machine under test,
and a fixture that only works on one bench printer is worse than an honest gap.
Step 5 exercises the same path through the farm, which is the version that matters.

Multi-colour dispatch — AMS slot mapping — is untested against hardware in any
form. Step 5 with a single-colour plate does not prove it.
