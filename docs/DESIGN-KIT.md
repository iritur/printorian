# The design kit, and what is left of it

The kit is twenty-one screens of static HTML in [`design/`](../design/README.md).
**Those files are the source of truth for what a screen shows** — this document
is not a transcription of them, and deliberately no longer tries to be.

What it carries instead is the part the HTML cannot: which screens exist in the
app, what the four that do not would need from the backend, and the conventions
that hold across all of them.

> Replaces `DESIGN-KIT-PLAN.md`, `DESIGN-KIT-INTEGRATION.md` and
> `DESIGN-KIT-BACKEND-GAPS.md`, which answered the same question from three angles
> and disagreed with each other and with the code. The per-screen inventories they
> carried for *built* screens are gone: the code is the truth for those now, and a
> second description of a finished screen is a second thing to keep in step.

Statuses below were read off the code — routes in each app's `App.tsx`, models in
`backend/printorian/contexts/` — not off a plan. Re-verify before trusting; that
is how all three predecessors went wrong.

*Last verified 2026-08-26.* The settings counts in §1 and §2.1 come from
`len(SECTIONS)` and `len(FIELDS)` in
[`contexts/settings/sections.py`](../backend/printorian/contexts/settings/sections.py)
and from the `kind` counts over `FIELDS`, evaluated rather than transcribed; the
screen's state comes from `App.tsx` rendering `SettingsPage` and from
`SettingsPage.test.tsx`. Deliberately **not** from `HANDOFF.md` — a status copied
from another document has only moved the drift.

---

## 1. Where the screens stand

**Seventeen of twenty-one are built.** Every public screen ships; the four that do
not are all control-realm. `settings` was the nearest of them and is now built —
102 parameters across fourteen sections, served and audited. What is left of it is
the table-valued settings and the diagnostics panel, not the screen (§2.1).

| Screen | Realm | State |
|---|---|---|
| `promo` `catalog` `configurator` `checkout` `cabinet` `account` `auth` `blog` `blog-post` | public | **built** — all nine |
| `dashboard` `orders` `fleet` `materials` `users` `postproduction` `packaging` | control | **built** |
| `settings` | control | **built** — scalars; the tables and Диагностика remain, §2.1 |
| `service` | control | **not built** — §2.2 |
| `purchasing` | control | **not built** — §2.3 |
| `store` | control | **not built** — §2.4 |
| `logistics` | control | **not built** — §2.5 |

`index.html` is the kit's own contents page, not a screen.

## 2. Settings, and the four that are not built

For §2.2–§2.5 the kit inventories are preserved verbatim, because for those the kit
*is* the spec, and each ends with what the backend already has. §2.1 is no longer
one of them — settings is built, so the code is the truth for it and this document
records only what is still owed. The numbering is kept as it was so that the
references to it from the tracker and from §2.5 keep pointing at the same place.

### 2.1 `settings.html` — built, minus the tables

**The screen exists.** [`SettingsPage.tsx`](../frontend/apps/console/src/SettingsPage.tsx)
renders it and [`contexts/settings`](../backend/printorian/contexts/settings/) serves
it: **102 parameters across fourteen sections**, over `GET /settings`,
`GET /settings/sections`, `GET /settings/history` and `PUT`/`DELETE /settings/{key}`,
gated on `MANAGE_SETTINGS`.

The catalogue is *derived*, not hand-listed — the pricing rates are read off
`dataclasses.fields(RateSnapshot)` and the scheduler weights off
`SchedulingPolicy`. That is the same argument ADR-0020 makes for `rates_to_dict`:
a hand-listed set of keys silently omits the next rate somebody adds, and a
settings screen missing a rate is worse than one that never had it, because it
looks complete.

One control per `kind`, all built — `integer` 31 · `decimal` 30 · `boolean` 15 ·
`enum` 15 · `string` 8 · `table` 2 · `secret` 1. The single secret,
`finance.yookassa_secret_key`, is write-only: stored encrypted and never read
back. Editing a row marks it dirty, reveals the previous value, offers a per-row
revert and counts into a save bar; each save writes an audited «было · стало»
that `GET /settings/history` serves back under **Обслуживание системы**. Two of
the kit's irreversible operations are built and guarded by typing the farm name
first — reset rates and drop telemetry.

**The store is read at the edge, not only stored.** Five resolutions run per
request or per worker pass rather than once at process start: `resolve_rates` and
`resolve_tiers` (pricing and orders), `resolve_promise` (SLA), `resolve_int` for
telemetry retention, and `resolve_scheduling` in the scheduler pass. A key with no
row resolves to the code default, so an empty table prices exactly as the farm
always did, and an order keeps the rate snapshot it was agreed at (ADR-0020) —
changing a margin moves the next quote and nothing already sold.

**What is still owed.** The kit's fifteenth section and the settings that are
*tables* rather than scalars:

- [#29](https://github.com/iritur/printorian/issues/29) — the six table-valued
  sections. The volume ladder and the customer tiers are built and are the
  pattern to copy, not to reinvent.
- [#30](https://github.com/iritur/printorian/issues/30) — **Диагностика**, the
  fifteenth section. Read-only, nothing in it is a setting, and it is the only
  place the health checks would be seen.
- [#31](https://github.com/iritur/printorian/issues/31) —
  «Очистить лист ожидания», the last unbuilt irreversible operation.
- [#32](https://github.com/iritur/printorian/issues/32) — worker loop intervals
  still take effect only on restart.

`design/settings.html` stays the reference for anything not yet built. For what is
built, the code is the truth — and per this document's own opening rule, a second
description of a finished screen is a second thing to keep in step.

### 2.2 `service.html`

Five ticket kinds: **установка · ремонт · ТО · загрузка материала · перемещение**.

- Ticket board with priority and elapsed time; «СООБЩИЛ ДРАЙВЕР» origin
- **Последствия** — what this ticket already costs
- **Порядок работ** — steps with norms
- Fleet reliability table: Машина · Состояние · Наработка · Ближайшее ТО ·
  Отказов/год · Надёжность
- **Причины отказов** — 90 суток · 18 случаев, funnel
- **Запчасти на посту** — Позиция · Остаток · Расход/мес · Статус
- Crew badges and marks; MTTR, fleet readiness

What backend exists: `ServiceOperation` with kind/interval/hours, наработка, ближайшее ТО.

**What the backend still owes:** [#33](https://github.com/iritur/printorian/issues/33) — tickets as an entity with steps, assignee, elapsed, consequence; failure causes, MTTR, отказов/год, надёжность; spare parts stock.

### 2.3 `purchasing.html`

- **Структура закупок** — funnel by class
- **Требуют заказа сейчас** — Позиция · Класс · Остаток · **Последствие**
- **Purchase orders** — Номер · Поставщик · Состав · Статус · Заказан · Ожидается ·
  Сумма
- **Supplier scorecards** — Поставщик · Поставок · В срок · Брак · Оборот · Оценка
- **Цены по ключевым позициям** — a year of price history
- PO detail: 6-stage path, line items, **Зачем этот заказ**, **Приёмка** (receiving
  into lots)

**What the backend still owes:** [#34](https://github.com/iritur/printorian/issues/34) — nothing exists; `PurchaseOrder`, `Supplier`, four purchasable classes.

### 2.4 `store.html`

- **Cell map** by zone — `.hv-node` per cell across zones A/B/C, brightness = fill
- **Movements today** — Время · Операция · Позиция · Откуда → куда · Кол-во ·
  Основание
- **Batches in a cell** — FIFO, oldest first: Партия · Принята · Сушка · Остаток
- **Turnover** — days on shelf per class; **dead stock** with the money in it
- **Stocktake** — Позиция · Ячейка · Лежит · Стоимость

What backend exists: `MaterialLot` with location.

**What the backend still owes:** [#35](https://github.com/iritur/printorian/issues/35) — cells, zones, drying state, movement ledger with reason, turnover, dead stock, stocktake.

### 2.5 `logistics.html`

- **Отгрузка сегодня** to the same cut-off as packaging
- **Carriers** — Перевозчик · Отправлений · В срок · Повреждений · Средняя цена
- **Зоны и тарифы** — *these land in the order's estimate*, so they are the same
  rows as the settings zones table (§2.1). Build them once.
- **Сроки доставки** — Зона · Обещано · Факт · Точность
- Shipment detail: 6-stage path, address from the cabinet, tracking history

**What the backend still owes:** [#36](https://github.com/iritur/printorian/issues/36) — nothing beyond `carrier_code` on a parcel; no `Shipment`, no carrier, no zone, no tracking.

## 3. Conventions every screen honours

Re-checked against the code; five of these were recorded as missing and are not.

| Convention | State |
|---|---|
| `data-realm` public/control split, hazard rail, realm badge | ✅ `applyRealm` |
| `data-theme` Void/Paper | ✅ `ThemeSwitch` |
| `data-state` machine states | ✅ |
| `data-tone` (`live`/`good`/`warn`/`bad`) — colour means machine state, never decoration | ✅ |
| `data-pri` task priority | ✅ |
| `data-tier` operator badges | ✅ |
| `data-sentiment` (spend up = bad, revenue up = good) | ✅ |
| Shared modal | ✅ `shell/Modal.tsx` |
| Sortable headers | ✅ `DataTable` — via props, not the kit's `data-sort-value` |
| Filter counter chips | ◐ ad-hoc per screen, not a shared component |
| `data-bind` live numeric readouts | ◐ React owns this; the attribute is not used |
| `data-tabs` / `data-tab-target` / `data-tab-panel` | ❌ CSS section 22 not ported |
| `data-auth-open` popup on any element | ❌ |
| Tabular figures, right-aligned, basis underneath | ✅ |

## 4. Backend capability nothing consumes

**This list has moved to the issue tracker.** Thirteen of the fourteen endpoints this section once carried now have consumers. The one that remains is tracked as:

- [#38](https://github.com/iritur/printorian/issues/38) — **`GET /materials/{code}`**, the materials detail popup

`TelemetrySample` was the headline entry here and no longer is: `metric_rollups` summarises it and `/fleet/metrics` serves it. `EstimateVariance` left the same way — `GET /jobs/variances` serves it and the order desk's «Пересмотр цены» panel reads it. So did `RateSnapshotRecord`: `GET /orders/{order_id}/rate-snapshot` serves it and «Тарифы заказа» reads it.

## 5. Order to build the rest in

```
settings ──┬──► logistics   (zones and tariffs are one table, defined in settings)
  (built)  └──► purchasing  (reorder thresholds and lead days are settings)
                            — both now wait on the #29 tables, not on the screen

purchasing ──► store        (receiving a PO is what puts a batch in a cell)

service      independent of all three — ServiceOperation already exists
```

**`settings` was first, and it is done.** That was not only because it was largest:
the other screens read parameters it owns, and building them against constants
would have meant building them twice. The edge that survives it is narrower — what
`logistics` waits on is the zones table specifically ([#29](https://github.com/iritur/printorian/issues/29)),
not the screen. **`service` any time** — it needs nothing the others produce.

`store` after `purchasing` is a preference rather than a hard edge: a cell map can
be built over lots that already exist, but receiving is where batches come from,
and a warehouse screen with no inbound is a screen with nothing to show.
