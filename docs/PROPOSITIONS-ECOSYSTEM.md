# Adopting the best of the Klipper ecosystem — a proposition list for Printorian

**Dated snapshot — 2026-08-28.** This document is an *argument*, not the tracker.
Per [WORKFLOW.md](WORKFLOW.md), open work lives in GitHub issues and a document
says what the system *is* while the tracker says what is *missing*. Every
proposition below is therefore written as something to be **triaged into an
issue** — adopted, deferred, or rejected — not as a plan that lives here. Once an
issue is filed, the issue owns it and this list is only the reasoning that got it
there.

**What this is.** Printorian is a *farm* layer: many printers, orders, pricing,
scheduling, dispatch, post-production — with a Bambu Lab driver and, in Phase 7,
a Moonraker driver to reach Klipper machines. The four projects studied are a
*single-printer* control stack: [Klipper](https://github.com/Klipper3d/klipper/)
(firmware), [Moonraker](https://github.com/Arksine/moonraker) (API server),
[Fluidd](https://github.com/fluidd-core/fluidd) and
[Mainsail](https://github.com/mainsail-crew/mainsail) (web UIs). Because they sit
one layer below us, the ideas that transfer are not their *features* but their
*realizations* — how they make an operator trust a machine they cannot see, and
how they keep one backend honest about many machines. That is the lens every
entry below uses.

---

## The propositions at a glance

| # | Proposition | Source | Theme | Priority |
|---|---|---|---|---|
| 1 | Per-printer terminal (raw command console) | Fluidd / Mainsail | Fleet ops | P1 |
| 2 | Live chamber camera per printer (no AI) | Fluidd / Mainsail | Fleet ops | P2 |
| 3 | Remote power control of printers | Moonraker | Fleet ops | P2 (hw) |
| 4 | Farm macros / runbook automation | Klipper + Fluidd + Mainsail | Automation | P1 |
| 5 | Named presets (material / print / QC) | Mainsail | Automation | P2 |
| 6 | Outbound notification adapters | Moonraker | Notifications | P1 |
| 7 | Component & health registry (Диагностика) | Moonraker | Observability | P2 |
| 8 | Environmental sensors (ambient, dry-box) | Moonraker | Observability | P3 (hw) |
| 9 | Heatmap-style telemetry surfaces | Mainsail | Observability | P3 |
| 10 | Printer profile library (reference configs) | Klipper | Config | P2 |
| 11 | Config validation with named errors | Klipper | Config | P3 |
| 12 | Settings import/export + diff + restart badge | Fluidd / Mainsail / Klipper | Config | P3 |
| 13 | Per-printer calibration/quality profiles | Klipper | Config | P3 |
| 14 | Job chaining / per-printer queue depth | Moonraker | Job intelligence | P1 |
| 15 | Plate/gcode metadata → thumbnails + estimates | Moonraker | Job intelligence | P2 |
| 16 | Sliced-plate / toolpath preview (G-code viewer) | Mainsail | Job intelligence | P2 |
| 17 | Job timelapse / photo capture for QC | Mainsail | Job intelligence | P3 |
| 18 | Print/job history viewer | Moonraker / Mainsail | Job intelligence | P2 |
| 19 | Machine-to-machine auth (tokens + trusted CIDR) | Moonraker | Security | P2 |
| 20 | Version/update manifest + rollback + firmware pin | Moonraker | Lifecycle | P3 |

*(hw) = needs hardware this repository cannot supply — file with `blocked:hardware`.*

---

## 1. Fleet operations — remote control and visibility

The farm's core daily problem is the same one Moonraker solves for one machine:
**an operator must be able to see and act on a printer they are not standing at.**
Printorian already has the state machine, telemetry, and normalized alerts; what
the four projects add is the *hands-on* surface.

### 1. Per-printer terminal (raw command console) — P1
**Source:** Fluidd / Mainsail "G-Code console".
**Printorian now:** the Bambu driver has a transport (`MQTT request/response`,
`FTPS`) and rich error codes (`error.driver.*`), but no screen lets an engineer
poke a single printer directly.
**Realization:** a per-printer "Терминал" tab in the fleet drawer that publishes a
raw MQTT request and shows the response, plus a read-only FTPS directory listing.
**Why now:** `4 of [HANDOFF.md](HANDOFF.md) — the driver has still never talked to
a real printer, and step 1 of the first-print runbook is "separate the network from
our code." A terminal is the cheapest instrument that makes that separation a
click instead of a spike script.
**Labels:** `area:drivers` · `area:frontend` · `type:task`.

### 2. Live chamber camera per printer (no AI) — P2
**Source:** Fluidd/Mainsail camera streams; the Bambu chamber camera is port 6000
([BAMBU-LAN-PROTOCOL.md `4](BAMBU-LAN-PROTOCOL.md)).
**Printorian now:** the camera port is *observed but uncharacterised*.
**Realization:** a read-only MJPEG/RSTP stream in the fleet drawer. Deliberately
**no** failure-detection ML — that is already out of scope in ROADMAP; this is just
"see the bed without walking over."
**Labels:** `area:drivers` · `area:frontend` · `type:task`.

### 3. Remote power control of printers — P2 (hw)
**Source:** Moonraker "power" devices — smart plugs over Tasmota/TP-Link/Shellies/MQTT.
**Printorian now:** a wedged printer needs a human with hands on the wall socket.
**Realization:** a `PowerDevice` per printer plus a provider interface (the same
pluggability shape as payment providers / carriers, ADR-0009); "Power-cycle P1S-03"
becomes an audited action on the fleet screen.
**Why:** the most common remedy for a hung Bambu is a power cycle, and it is the
one action no software can currently perform.
**Labels:** `area:drivers` · `area:backend` · `type:task` · `blocked:hardware`.

---

## 2. Operator automation — runbooks and macros

Klipper's most copied idea is not a motion feature, it is **g-code macros**: named,
composable, user-written sequences that make a one-off command repeatable and safe.
A farm has the same need one layer up — "reprint this order", "clear the finished
plate", "load filament" — and nothing for it.

### 4. Farm macros / runbook automation — P1
**Source:** Klipper macros + Fluidd's macro panel + Mainsail's presets.
**Printorian now:** the scheduler, the SLA sweep and the intake worker run *fixed*
policy; there is no operator-definable action layer.
**Realization:** a small **runbook** capability: named, versioned, audited sequences
of farm actions ("Repeat order #4127 on the same plate", "Park and clear finished
plates", "Start-of-day: purge + bed check"), surfaced as buttons on the order and
fleet screens and *triggerable by events* ("on `printer.error` → park + notify").
This is the difference between an alarm and a *response* to the alarm.
**Labels:** `area:backend` · `area:frontend` · `type:task`.

### 5. Named presets — material / print / QC templates — P2
**Source:** Mainsail temperature/speed presets.
**Printorian now:** material specs carry temps and the pricing engine holds rates,
but nothing stores *reusable operator recipes*.
**Realization:** named templates for material print parameters, per-model print
profiles, and post-production QC checklists (which Phase 5 needs anyway), so a
recipe is picked rather than re-typed — fewer places to silently get a number wrong.
**Labels:** `area:backend` · `area:frontend` · `type:task`.

---

## 3. Notifications — get the alarm off the screen and onto the operator

Moonraker's single most useful realization is its **notification bus**: one event
fan-out to many channels (Apprise, Telegram, Discord, email, ntfy, MQTT…). Printorian
has the event half and is missing the channel half.

### 6. Outbound notification adapters — P1
**Source:** Moonraker notifications.
**Printorian now:** `attention.*` events reach the personnel dashboard over a
WebSocket, which requires someone looking at a screen. Scenario C10 says the system
"alarms personnel" — an alarm that only exists on a dashboard is not an alarm.
**Realization:** a notification provider interface (again, ADR-0009 shape) with
Telegram/Discord/email first; route `attention.raised`, `printer.error`,
`material.low`, `sla.at_risk` and `print finished` to an operator's phone.
**Not** a chatbot — one-way alerting, which is outside the "messenger bots" scope cut.
**Labels:** `area:backend` · `type:task`.

---

## 4. Observability — a health surface that tells the truth

Moonraker maintains a **component registry**: every subsystem (Klippy, the file
manager, each configured sensor, each update target) reports a status, version and
error, and one endpoint serves the whole picture. That is exactly what Printorian's
unbuilt Диагностика screen is for, and the fleet's many parts need it.

### 7. Component & health registry (Диагностика) — P2
**Source:** Moonraker component registry.
**Printorian now:** `/health/ready` has five checks, each worker beats at end of
pass, and the settings screen's fifteenth section — Диагностика — is unbuilt (#30).
**Realization:** build Диагностика as a registry, not a list: per printer (state +
driver version + last telemetry), per AMS, per worker (last beat vs now), plus
postgres/redis/backup/edge — each carrying a status and a version, so "is the farm
healthy" and "what is running which version" are the same screen.
**Labels:** `area:backend` · `area:frontend` · `type:task` — builds on #30.

### 8. Environmental sensors — ambient + dry-box humidity — P3 (hw)
**Source:** Moonraker sensors (DS18B20, etc.).
**Printorian now:** per-printer nozzle/bed temps only; the store.html "drying state"
and "dryness" fields exist on `MaterialLot` with no instrument behind them.
**Realization:** an environment-sensor context (farm ambient temperature/humidity,
dry-box humidity) that feeds drying state and later failure correlation (Phase 6).
**Labels:** `area:backend` · `type:task` · `blocked:hardware`.

### 9. Heatmap-style telemetry surfaces — P3
**Source:** Mainsail's bed-mesh heightmap.
**Printorian now:** the 7×24 load map is already a heatmap over `metric_rollups`
(and unmeasured hours are hatched, not invented — the right behaviour).
**Realization:** generalize that surface to other per-printer quality telemetry
(bed/chamber temperature maps where the hardware reports them, success/failure
density per printer over time) — the *presentation* idea, not the bed-mesh feature,
which is single-printer territory.
**Labels:** `area:frontend` · `type:task`.

---

## 5. Config and profile management

Klipper's configuration model — **the whole machine is one declarative, validated
document, restart to apply, with errors that name the line** — is its other famous
realization. Printorian's structured, audited settings are already *better* than
editing `printer.cfg`; what is worth taking is the surrounding discipline.

### 10. Printer profile library (reference configs) — P2
**Source:** Klipper's reference configs per printer model.
**Printorian now:** `Capabilities` (build volume, nozzle, AMS slots) must be
entered per machine.
**Realization:** a library of known-good `Capabilities` templates per model
(X1C, P1S, X2D, A1, Elegoo…), so onboarding a printer is "pick model → verify serial
+ access code" rather than hand-typing numbers — fewer transcription errors in the
numbers the scheduler filters on.
**Labels:** `area:backend` · `area:frontend` · `type:task`.

### 11. Config validation with named, actionable errors — P3
**Source:** Klipper's startup/config errors.
**Printorian now:** the save bar already names a failing field, and drivers already
raise *typed* errors (`DriverStorageError` = "printer 7 has no usable storage").
**Realization:** a validation report that runs on save *and* on boot/connect and
names every invalid or inconsistent setting by key and remedy — the discipline, not
new fields.
**Labels:** `area:backend` · `type:task`.

### 12. Settings import/export + diff + "takes effect on restart" badge — P3
**Source:** Fluidd/Mainsail config editor + Klipper restart-to-apply.
**Printorian now:** settings are audited «было · стало», but some (loop intervals)
take effect only on restart (#32), and there is no export/diff for a farm.
**Realization:** export the non-secret settings, diff two snapshots, and badge any
changed key that needs a restart — surfacing #32's honesty in the UI.
**Labels:** `area:backend` · `area:frontend` · `type:task`.

### 13. Per-printer calibration/quality profiles — P3
**Source:** Klipper input shaper / pressure advance — per-machine calibration stored
with the machine.
**Printorian now:** the estimator is expected to be wrong and is calibrated only in
aggregate (Phase 6).
**Realization:** store per-printer calibration (flow, tuned material temps) and let
it feed the estimate-vs-actual loop, so the estimator corrects *per machine* rather
than only per farm.
**Labels:** `area:backend` · `type:task`.

---

## 6. Job intelligence and analytics

Moonraker and Mainsail between them close the loop from "a gcode file" to "a
finished print with a thumbnail, a history and statistics." Printorian has the
data (jobs, telemetry, `metric_rollups`) and is missing the *reading* of it.

### 14. Job chaining / per-printer queue depth — P1
**Source:** Moonraker's job queue.
**Printorian now:** the scheduler assigns one job at a time to idle machines;
queue depth is the named Phase 4 gap in [ARCHITECTURE `6](ARCHITECTURE.md) —
changeover cost is currently zero for every candidate because every eligible
machine is idle.
**Realization:** allow planning onto a still-busy machine (a printer-local queue
with a known finish time), which makes changeover cost and batching affinity real
terms again — the exact terms the ARCHITECTURE note says become reachable this way.
**Labels:** `area:backend` · `type:task`.

### 15. Plate/gcode metadata → thumbnails + estimates — P2
**Source:** Moonraker's gcode metadata extraction (thumbnail, print time, filament).
**Printorian now:** `PreparedPlate` parses *truth* (minutes, grams, layer count),
but the console's prep and library listings carry no thumbnail, and the mesh
estimator has no per-model feedback loop on the frontend.
**Realization:** extract a thumbnail and the sliced time/filament estimate at prep
time for the prep and library screens, and pair them with `EstimateVariance`
(already persisted, unserved — #39) so an engineer sees the delta at a glance.
**Labels:** `area:backend` · `area:frontend` · `type:task`.

### 16. Sliced-plate / toolpath preview (G-code viewer) — P2
**Source:** Mainsail's G-code viewer.
**Printorian now:** the storefront already renders the uploaded STL in 3D
(`apps/web/src/ModelViewer.tsx` — three.js `STLLoader` + `OrbitControls`, bundled
not CDN). What it cannot do is show the *sliced* result — the plate and toolpath
the printer will actually run.
**Realization:** a read-only preview of the `PreparedPlate` toolpath (Mainsail's
G-code-viewer analog) in the prep screen, so an engineer verifies support, brim
and orientation rather than only the model. Preview is **not** a slicer — an
in-app slicer is already out of scope; this is a renderer.
**Labels:** `area:frontend` · `type:task`.

### 17. Job timelapse / photo capture for QC — P3
**Source:** Mainsail + moonraker-timelapse.
**Printorian now:** Phase 5 wants photos in post-production; nothing captures them
during the print.
**Realization:** capture chamber stills per job and attach them to the job/QC record
so a failed print is diagnosed from evidence, not memory.
**Labels:** `area:backend` · `type:task`.

### 18. Print/job history viewer — P2
**Source:** Moonraker print history; Mainsail/Fluidd history views.
**Printorian now:** order/job lifecycle, `metric_rollups`, throughput and occupancy
exist; there is no per-printer/per-job *history* screen with thumbnails and
statistics (success rate, failures by printer/material/model).
**Realization:** a job history screen that feeds — and is the first deliverable of —
the Phase 6 failure taxonomy and success-rate analytics.
**Labels:** `area:backend` · `area:frontend` · `type:task`.

---

## 7. Security and lifecycle

### 19. Machine-to-machine auth — API tokens + trusted-client (CIDR) policy — P2
**Source:** Moonraker's authorization model (JWT + API keys + IP allow/deny + CORS).
**Printorian now:** human RBAC with `VIEW_FINANCIALS` separation; there is no
credential for a *machine* (the edge VPS tunnel, a future integration, a driver-side
callback).
**Realization:** scoped API tokens for service-to-service calls and a trusted-client
CIDR allowlist for the LAN/edge, alongside — never replacing — the existing RBAC.
**Labels:** `area:backend` · `area:security` · `type:task`.

### 20. Version/update manifest + rollback + firmware pin — P3
**Source:** Moonraker's update manager (component versions, update, rollback).
**Printorian now:** CI/CD and images are signed (cosign); there is no in-app view of
*what version each component runs*, and the "pin known-good firmware" risk
mitigation is prose, not a record.
**Realization:** a component-version manifest surfaced in Диагностика (`7), with a
rollback path and a recorded firmware pin per printer — turning the Bambu-firmware
risk into a visible, reversible state.
**Labels:** `area:backend` · `area:infra` · `type:task`.

---

## Printorian is already ahead — no adoption needed

The four projects are excellent at one printer; Printorian is ahead of all of them
in the *farm* concerns, and it is worth saying so explicitly so none of these get
"adopted" backwards:

- **The pricing engine** (pure, deterministic, versioned, with per-order rate
  snapshots — ADR-0002, ADR-0020) has no equivalent anywhere in the four projects.
- **The scheduler's `AssignmentDecision` audit** — "why did job #4127 go to
  P1S-03" answered from the database, with the machines that lost and why — is far
  beyond Moonraker's job queue.
- **Drivers never simulate** (ADR-0007), enforced with contract tests and a virtual
  farm, is a discipline none of the four projects even names.
- **The `PreparedPlate` cache** is Klipper's virtual SD card + Moonraker's file
  manager, but digest-keyed, versioned and carrying provenance — the thing that
  makes human-gated slicing scale.
- **Structured, audited settings read at the edge** beat editing `printer.cfg` in
  a text box, and `Money`/`Decimal` + `VIEW_FINANCIALS` separation beat
  Moonraker's SQLite-by-convention.
- **"Invalidation, not state" over WebSocket** (ADR-0015) is the stronger
  consistency answer than Moonraker's state-push JSON-RPC.

## Deliberately *not* adopting

Recorded so they are decisions rather than oversights:

- **Free-form theme editor** (Fluidd/Mainsail) — conflicts with Printorian's
  `data-tone` discipline, where colour *means* machine state. Adopting it would
  let an operator make "error" look like "idle."
- **Spoolman integration** — Printorian's `MaterialLot` (FIFO, dryness, AMS
  mapping) is already richer; QR spool *labelling* could be adopted, the external
  tool cannot.
- **Moonraker's JSON-RPC / OctoPrint-compat layer** — REST + invalidation is a
  locked decision (ADR-0015), and OctoPrint compatibility is irrelevant.
- **Klipper firmware or Moonraker wholesale** — Printorian is the layer *above* the
  printer; the Moonraker *driver* (Phase 7) is how we talk *to* Klipper printers,
  not a backend we adopt.
- **In-app slicer / AI camera failure detection / messenger bots** — already out of
  scope in ROADMAP; none of the above reintroduces them.

---

## Triage next

The right next step per [WORKFLOW.md](WORKFLOW.md) is to file one issue per
proposition (or one per theme), with the priority, area and any `blocked:hardware`
label above, and let the tracker own them. The P1 set to start with: **#1 terminal,
#4 macros, #6 notifications, #14 queue depth.**

## Sources

- Klipper — https://github.com/Klipper3d/klipper · https://www.klipper3d.org/Config_Reference.html
- Moonraker — https://github.com/Arksine/moonraker · https://moonraker.readthedocs.io/ (job queue, notifications, machine, authorization)
- Fluidd — https://github.com/fluidd-core/fluidd · https://docs.fluidd.xyz/
- Mainsail — https://github.com/mainsail-crew/mainsail · https://docs.mainsail.xyz/
