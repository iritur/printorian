# The design kit, and what is left of it

The kit is twenty-one screens of static HTML in [`design/`](../design/README.md).
**Those files are the source of truth for what a screen shows** — this document
is not a transcription of them, and deliberately no longer tries to be.

What it carries instead is the part the HTML cannot: which screens exist in the
app, what the five that do not would need from the backend, and the conventions
that hold across all of them.

> Replaces `DESIGN-KIT-PLAN.md`, `DESIGN-KIT-INTEGRATION.md` and
> `DESIGN-KIT-BACKEND-GAPS.md`, which answered the same question from three angles
> and disagreed with each other and with the code. The per-screen inventories they
> carried for *built* screens are gone: the code is the truth for those now, and a
> second description of a finished screen is a second thing to keep in step.

Statuses below were read off the code — routes in each app's `App.tsx`, models in
`backend/printorian/contexts/` — not off a plan. Re-verify before trusting; that
is how all three predecessors went wrong.

---

## 1. Where the screens stand

**Sixteen of twenty-one are built.** Every public screen ships; the five that do
not are all control-realm. `settings` is the nearest of them — its backend store
exists and serves the pricing rates, and what is missing is the screen and the
other fourteen sections.

| Screen | Realm | State |
|---|---|---|
| `promo` `catalog` `configurator` `checkout` `cabinet` `account` `auth` `blog` `blog-post` | public | **built** — all nine |
| `dashboard` `orders` `fleet` `materials` `users` `postproduction` `packaging` | control | **built** |
| `settings` | control | **store built, screen not** — §2.1 |
| `service` | control | **not built** — §2.2 |
| `purchasing` | control | **not built** — §2.3 |
| `store` | control | **not built** — §2.4 |
| `logistics` | control | **not built** — §2.5 |

`index.html` is the kit's own contents page, not a screen.

## 2. The five that are not built

Kit inventories preserved verbatim, because for these the kit *is* the spec. Each
ends with what the backend already has.

### 2.1 `settings.html` — 15 sections, ~100 parameters

The largest single gap in the product, and the one every other screen leans on.

**The store exists; the screen does not.** `contexts/settings` serves the seventeen
scalar **Ценообразование** rates through `GET/PUT/DELETE /settings`, with the audit
the section below requires. A key with no row resolves to the code default, so an
empty table prices exactly as the farm always did, and an order keeps the rate
snapshot it was agreed at (ADR-0020) — changing a margin moves the next quote and
nothing already sold.

The remaining ~85 parameters are still constants, on `core.config.Settings`. They
are read once at process start rather than per request, so moving them into the
table changes *when* they are read as well as where they come from; that is the
next piece of work here, and it is bigger than it looks for that reason.

The kit's identifiers are the real ones:

| Section | Parameters |
|---|---|
| **Общие** | `farm_name` `farm_timezone` `farm_open_hour` `farm_close_hour` `unattended_printing` `currency` `default_locale` `units` |
| **Ценообразование** | `labor_rate_per_hour` `labor_hours_per_print_hour` `labor_hours_per_job` `engineering_hours_per_resize` `postprocess_rate_per_hour` `electricity_rate_per_kwh` `printer_power_kw` `depreciation_per_printer_hour` `material_procurement_flat` `multicolor_purge_grams_per_extra_color` `overhead_per_print_hour` `failure_buffer_percent` `rush_surcharge_percent` `margin_percent` |
| **Скидки и тарифы** | `guard_tier_cliffs`; volume ladder table (Ступень · От количества · Скидка · Цена за шт); customer tiers table (Код · Название · Скидка · Прибыль·переопределение · Клиентов) |
| **Планировщик** | `weight_capability_waste` `weight_material_headroom` `weight_amortization` `weight_load_balance` `due_soon_hours` `load_horizon_minutes` `expensive_per_hour` `comfortable_headroom` `scheduler_tick_seconds` `waitlist.no_capable_printer` `waitlist.awaiting_capacity` `waitlist.material_not_loaded` |
| **Сроки и SLA** | `promise_buffer_percent` `min_lead_hours` `rush_lead_hours` `percent_per_day` `max_percent` `sla_sweep_seconds` `sla_auto_refund` `price_variance_tolerance` `price_review_role` |
| **Склад и материалы** | `low_stock_grams` `critical_stock_grams` `auto_reorder` `default_lead_days` `require_drying` `drying_valid_hours` `writeoff_below_grams` `track_lots` |
| **Оборудование и сервис** | `telemetry_poll_seconds` `driver_timeout_seconds` `driver_send_retries` `pause_on_hms_error` `allow_mock_driver`; maintenance-interval table (Операция · Код · Периодичность · Простой · Расход) |
| **Постобработка** | `require_quality_check` `photo_before_packing`; operations catalogue (Операция · Код · Нормо-часы·база · На см² поверхности · Доступна) |
| **Логистика** | `packaging_per_unit` `shipping_flat` `volumetric_divisor` `free_shipping_threshold`; zones table (Зона · Перевозчик · Базовая · За кг · Срок · Активна) |
| **Финансы** | `tax_regime` `vat_percent` `prices_include_tax` `rounding_step` `payment_provider` `yookassa_shop_id` `yookassa_secret_key` `prepayment_percent` `invoice_payment` `invoice_due_days` `refund_before_print_percent` `refund_after_print_percent` `refund_approval_threshold` |
| **Уведомления** | `mail_from` `smtp_host` `telegram_chat_id` `quiet_hours`; event matrix (Событие · Код · Почта · Экран цеха · Telegram) × 9 events |
| **Доступ и безопасность** | `session_ttl_hours` `password_min_length` `password_hasher` `require_2fa_for_management` `lockout_attempts` `audit_retention_days`; permission matrix; API keys table |
| **Интеграции** | `slicer_engine` `slicer_path` `slicer_profile` `slicer_timeout_seconds` `bambu_connection` `bambu_cloud_account` `bambu_transport`; webhooks table |
| **Диагностика** | Read-only: 12 `.hv-health` subsystem checks with latency, versions, last log lines. **Nothing here is a setting** |
| **Обслуживание системы** | `backup_enabled` `backup_hour` `backup_retention` `backup_path` `model_retention_days` `telemetry_retention_days` `maintenance_mode`; **change audit log** (Время · Кто · Параметр · Было · Стало) |

Interaction the screen requires: editing a row **marks it dirty**, reveals the
previous value (`.hv-set__was`, «БЫЛО 9»), offers a per-row revert, and counts into
a save bar (`data-dirty`). Secrets are write-only — `yookassa_secret_key` shows
«КЛЮЧ СОХРАНЁН · Заменить» and can never be read back.

Two things make this bigger than a CRUD screen. Changing a rate must not silently
reprice work already quoted — ADR-0020 persists a rate snapshot per order for
exactly this reason, and the settings store has to respect it. And the audit log
is part of the feature, not an extra: «Было · Стало» is what makes a farm able to
answer why a price changed last Tuesday.

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

| Element | Backing |
|---|---|
| `ServiceOperation` with kind/interval/hours | ✅ |
| Наработка, Ближайшее ТО | ✅ |
| **Tickets** as an entity, with steps, assignee, elapsed, consequence | ❌ |
| Failure causes, MTTR, отказов/год, надёжность | ❌ no failure record |
| Spare parts stock | ❌ inventory only knows filament |

### 2.3 `purchasing.html`

- **Структура закупок** — funnel by class
- **Требуют заказа сейчас** — Позиция · Класс · Остаток · **Последствие**
- **Purchase orders** — Номер · Поставщик · Состав · Статус · Заказан · Ожидается ·
  Сумма
- **Supplier scorecards** — Поставщик · Поставок · В срок · Брак · Оборот · Оценка
- **Цены по ключевым позициям** — a year of price history
- PO detail: 6-stage path, line items, **Зачем этот заказ**, **Приёмка** (receiving
  into lots)

**❌ Nothing.** No `PurchaseOrder`, no `Supplier`. Four purchasable classes:
materials, spare parts, packaging, printers.

### 2.4 `store.html`

- **Cell map** by zone — `.hv-node` per cell across zones A/B/C, brightness = fill
- **Movements today** — Время · Операция · Позиция · Откуда → куда · Кол-во ·
  Основание
- **Batches in a cell** — FIFO, oldest first: Партия · Принята · Сушка · Остаток
- **Turnover** — days on shelf per class; **dead stock** with the money in it
- **Stocktake** — Позиция · Ячейка · Лежит · Стоимость

| Element | Backing |
|---|---|
| `MaterialLot` with location | ◐ location exists; **cells and zones are not a model** |
| Drying state, `require_drying`, `drying_valid_hours` | ❌ |
| Movement ledger with reason | ❌ |
| Turnover, dead stock, stocktake | ❌ |

### 2.5 `logistics.html`

- **Отгрузка сегодня** to the same cut-off as packaging
- **Carriers** — Перевозчик · Отправлений · В срок · Повреждений · Средняя цена
- **Зоны и тарифы** — *these land in the order's estimate*, so they are the same
  rows as the settings zones table (§2.1). Build them once.
- **Сроки доставки** — Зона · Обещано · Факт · Точность
- Shipment detail: 6-stage path, address from the cabinet, tracking history

**❌ Nothing** beyond `carrier_code` on a parcel. No `Shipment`, no carrier, no
zone, no tracking.

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

The long list this section used to carry is spent — thirteen of its fourteen
endpoints now have consumers. What is left:

- **`GET /materials/{code}`** — the materials detail popup. The console reads
  `/materials` and `/materials/lots` and never the per-code detail.
- **`EstimateVariance`** — drives `price_review` and the desk's «Пересмотр цены»
  filter. Persisted, no endpoint.
- **`RateSnapshotRecord`** — persisted per ADR-0020; the menu advertises
  «Снимок тарифов». Nothing serves it.

`TelemetrySample` was the headline entry here and no longer is: `metric_rollups`
summarises it and `/fleet/metrics` serves it.

## 5. Order to build the rest in

```
settings ──┬──► logistics   (zones and tariffs are one table, defined in settings)
           └──► purchasing  (reorder thresholds and lead days are settings)

purchasing ──► store        (receiving a PO is what puts a batch in a cell)

service      independent of all three — ServiceOperation already exists
```

**`settings` first**, and not only because it is largest: four of the five screens
read parameters it owns, and building them against constants means building them
twice. **`service` any time** — it needs nothing the others produce.

`store` after `purchasing` is a preference rather than a hard edge: a cell map can
be built over lots that already exist, but receiving is where batches come from,
and a warehouse screen with no inbound is a screen with nothing to show.
