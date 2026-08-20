# Integrating the design kit into the web app

Every screen, list, table, control and parameter in [`design/`](../design/README.md),
checked against what the running apps and the backend actually do today, and the
work each one implies.

This supersedes the screen inventory in
[DESIGN-KIT-PLAN.md](DESIGN-KIT-PLAN.md) and
[DESIGN-KIT-BACKEND-GAPS.md](DESIGN-KIT-BACKEND-GAPS.md), both of which were
written when the kit had **fourteen** screens. It now has **twenty-one**, and the
seven that arrived since — post-production, packaging, service, purchasing, store,
logistics, promo — are the shop floor and the supply chain, which is the half of
the system with the least backend behind it. The two older documents remain
correct about what they cover; they are simply no longer the whole map.

Compiled by grepping the kit and the code, not by reading the mockups. "Missing"
means *searched for and not found*.

> **Implementation status.** §1.5's client regeneration and health strip, all of
> §3.1 (the realm split), all of §3.2 (the overlay's territories), and the event
> and fleet-column items of §1.5 are **done and verified** — see §8. Everything
> else below is still ahead.

---

## 0. Scope, and where the screens land

**"The web app" is `frontend/`, not one bundle.** ADR-0016 splits the running
system into two apps, and the kit draws the same line itself: every screen carries
`data-realm="public"` or `data-realm="control"` on `<html>`, and the nav overlay
counts them — *Витрина 7 · Пульт 13*. Routing a control screen into the storefront
bundle would put the farm's margin figures and printer credentials into the
JavaScript served to anonymous visitors. So:

| Kit realm | Lands in | Screens |
|---|---|---|
| `public` (витрина) | `frontend/apps/web` | promo, catalog, configurator, checkout, auth, account, cabinet, blog, blog-post |
| `control` (пульт) | `frontend/apps/console` | dashboard, orders, fleet, materials, users, settings, postproduction, packaging, service, purchasing, store, logistics |
| — | neither | `index.html` — the component reference, not a product screen |

Shared vocabulary — chrome, nav overlay, tables, price leaders, status tags,
formatting, i18n — lives in `packages/ui` and is built once for both.

If the intent really is a single bundle serving both realms, that is a decision to
reopen ADR-0016 with, and it changes §4 substantially. Everything else in this
document holds either way.

---

## 1. Where things actually stand

*This section is the audit that produced the plan, kept as written. §1.2 and §1.3
have since been acted on — see §8 for what changed.*

### 1.1 Screens

| Kit screen | Realm | App screen today | State |
|---|---|---|---|
| `promo.html` | public | `PromoPage.tsx` | Built, styled |
| `configurator.html` | public | `ConfiguratorPage.tsx` | Built, **old tokens** |
| `checkout.html` | public | `CheckoutPage.tsx` | Built, **old tokens** |
| `cabinet.html` | public | `CabinetPage.tsx` (27 lines) | Thin — wraps `OrdersScreen`, which does render the breakdown and event history; the kit's 9-stage `.hv-pipe`, leaders and layout are absent |
| `orders.html` | control | `OrderDesk.tsx` / `OrdersPage.tsx` | Built, **old tokens**, no margin panel |
| `fleet.html` | control | `FleetPage.tsx` / `FleetAdmin.tsx` | Built, **old tokens**, no 4-tab detail popup |
| `materials.html` | control | `MaterialsPage.tsx` | Built, **old tokens** |
| `users.html` | control | `UsersPage.tsx` | Built, **old tokens**, no sessions panel |
| — | control | `PrepPage.tsx` | **Exists with no kit screen.** Needs one drawn, or folding into the order desk |
| `catalog.html` | public | `CatalogPage.tsx` | **Built** — search, 5 facet groups, 8 sort keys, grid/list, detail panel with 3D view |
| — | control | `LibraryPage.tsx` | **Built, no kit screen** — catalogue curation, gated on `manage_library` |
| `account.html` | public | — | **Not built** |
| `auth.html` | public | `AuthPanel` (inline only) | **Not built as a screen or a popup** |
| `blog.html`, `blog-post.html` | public | — | **Not built** |
| `dashboard.html` | control | `dashboard/DashboardPage.tsx` | **Built** — synced section for section with the kit |
| `settings.html` | control | — | **Not built** |
| `postproduction.html` | control | `postproduction/PostProductionPage.tsx` | **Built** — board, per-step norms, scorecards |
| `packaging.html` | control | `packaging/PackagingPage.tsx` | **Built** — cutoff countdown, box recommendation, tara ledger |
| `service.html` | control | — | **Not built** |
| `purchasing.html` | control | — | **Not built** |
| `store.html` | control | — | **Not built** |
| `logistics.html` | control | — | **Not built** |

**12 built (2 of them partial), 10 missing, 1 extra app screen with no kit design.**

### 1.2 Stylesheets

`design/css/harvester.css` is 3 060 lines in 22 numbered sections plus a rail.
What has been promoted into `packages/ui/src/harvester/`:

| Kit source | Ported to | Imported by an app? |
|---|---|---|
| `tokens.css` | `tokens.css` | ✅ both |
| sections 01–14 (system) | `system.css` (1 257 lines) | ✅ both, via `index.css` |
| `menu.css` | `menu.css` | ✅ both |
| section 21 (promo) | `promo.css` | ✅ web only |
| **sections 15–20** (article, settings, dashboard, catalogue, account/auth, operations) | `screens.css` (1 610 lines) | ❌ **not imported anywhere** |
| **section 22** (tab switching, `[data-tab-panel]`) | — | ❌ **not ported** |
| **`realm.css`** (the public/control split, hazard rail, realm badge) | — | ❌ **not ported** |

So `screens.css` exists, is complete, and is dead code: `index.css` deliberately
omits it and neither `main.tsx` imports it. Every screen it styles is a screen
that does not exist yet, which is why — but the moment one is built, one import
line is the whole styling task for it.

**`--pr-*` bridge tokens: 250 lines still reference one** (273 occurrences),
exactly where DESIGN-KIT-PLAN.md predicted — 119 lines in `apps/web/src/app.css`,
131 in `apps/console/src/console.css`. Slice C has not started. Both stylesheets are
still written in the pre-Harvester vocabulary and are the honest progress meter.

### 1.3 The shell

`AppShell` draws the chrome, the path strip, the app bar and the status bar for
both apps. Against the kit it is missing:

- **`data-realm` is never set.** No hazard rail, no graph-paper ground for control
  only (`hv-graph` is applied unconditionally), no density change, no realm badge.
  The kit's entire public/control signalling system is absent.
- **The realm badge** (`.hv-realm`, `.hv-realm__flag`) — the chip that says which
  side you stand on and opens the menu filtered to the *other* side.
- **`RU`/`EN` sits in the app bar.** The kit puts it in the OS bar, immediately
  right of `PRINTORIAN OS ./v2.0`, because the choice is a property of the console
  and not of the section.
- **`STATUS :: ONLINE` is hardcoded** in the path strip.

`NavOverlay` implements the filter box, arrow keys, Enter/Esc, permission
filtering and opening-on-current. Against `design/js/menu.js` it is missing:

- The **territory split** — `ВИТРИНА` above, `ПУЛЬТ` below, the hatched
  `ГРАНИЦА ДОСТУПА · ТРЕБУЕТСЯ РОЛЬ` border between
- The **three realm filters** (`Всё` / `Витрина 7` / `Пульт 13`) with counts
- **Per-territory numbering** that restarts, and the per-row flag chip
- **Locked rows** — `data-locked`, dimmed rather than hidden, so you can see the
  role you would need
- The **preview pane**: `mark`, `kicker`, prose, three stats, and the schematic
  line drawing per destination (7 shapes)
- The **open sequence** — backdrop scale, corner brackets, one scan pass, 34 ms
  entry stagger, label decode; all collapsed under `prefers-reduced-motion`

`AuthPanel` exists as an inline component. The kit's `js/auth.js` makes the same
three modes a **popup injectable on every page** via `data-auth-open="signin|signup|recover"`,
which matters most at checkout, where navigating to a login page loses a
configured quote.

### 1.4 The backend

Nine contexts exist: `catalog`, `fleet`, `identity`, `inventory`, `ordering`,
`payments`, `pricing`, `production`, `scheduling`. Endpoints today:

```
auth      register · sign-in · sign-out · me
users     list · create · set role · set active
orders    create · mine · list · overdue · get · queue · advance
payments  create · webhook · by-order · get · refund · providers · settle-manually
pricing   quote · preview
printers  list · get · create · update · update-state · delete · service ops (add, complete)
materials list · recommend · get · create-spec · add-lot
jobs      prep-queue · wait-list · plates/find · get · decisions · list · create · plate · release
public    stats
health    health · ready
```

Nothing serves settings, post-production, packaging, purchasing, warehouse,
logistics, the journal, the model library, dashboard aggregates, sessions,
password recovery, addresses, saved payment methods or notification preferences.

---

## 1.5 Backend capability that already exists and nothing uses

The cheapest work in this whole document. **Of the 46 paths the API serves, the
frontend calls 24.** The other 22 are built, tested and unreachable from any
screen — and several of them are exactly what a kit element needs.

### Endpoints with no consumer

| Endpoint | Kit element it already serves |
|---|---|
| `GET /materials/recommend` | configurator `01 :: Материал` → **«По сценарию»**. §2.3 calls the scenario dialog "new UI" — the *backend half is done*, only the dialog is missing |
| `GET /orders/overdue` | dashboard **«Требует внимания»**, the desk's risk chips, the menu's `Риск просрочки 2` |
| `GET /jobs/wait-list` | the desk's **«Лист ожидания 3»**, the funnel, and the three `waitlist.*` settings rows |
| `GET /jobs/{id}/decisions` | **`AssignmentRecord`** — all seven machines considered and the grounds for each rejection (`reject.busy`, `reject.material_not_loaded`, `reject.colour_not_loaded`). Phase 4's exit criterion, and **no screen shows it** |
| `GET /health`, `GET /health/ready` | the chrome's `STATUS ::` strip (**hardcoded `ONLINE`** in `AppShell.tsx:98`) and settings §Диагностика |
| `GET /payments/providers/available` | checkout `03 :: Оплата` — the provider radios are hardcoded |
| `GET /orders/{id}` | the desk's order popup, the cabinet's single-order view |
| `GET /materials/{code}` | the materials detail popup |
| `GET /printers/{id}`, `GET /printers/{id}/slots` | fleet popup tabs `spec` and `ams` |
| `PUT /printers/{id}`, `PUT /printers/{id}` (state), `DELETE /printers/{id}` | fleet **Параметры** / decommission — `FleetAdmin` only creates |
| `POST /payments/{id}/settle-manually` | the desk's manual settlement |
| `POST /jobs/{id}/release`, `POST /jobs/{id}/plate` | prep / desk actions |
| `GET /jobs/plates/find` | configurator: "this configuration is already prepared" — the ADR-0006 cache hit that decides whether an order dispatches by itself |
| `GET /users/{id}` | the users detail rail |

`POST /payments/webhook/{provider}` is correctly unused — it is server-to-server.

### Data persisted with no endpoint at all

Worse than unused: collected, retained, and unreachable.

- **`TelemetrySample`** — month-partitioned history carrying `nozzle_temp_c`,
  `bed_temp_c`, `progress_percent`, `layer_current`/`layer_total`,
  `remaining_minutes`, `error_code`, with a retention module behind it. Its own
  docstring names the three things it exists for: retention/rollups, **the
  dashboard's twelve-hour schedule with a live now-line**, and Phase 6's true P&L
  from real electricity readings. **There is no API surface on it whatsoever.**
  The printer popup's Сопло/Стол readings, the 7×24 heat grid and the gantt are
  all storage-complete and unserved
- **`EstimateVariance`** — what drives `price_review`. No endpoint. The desk's
  «Пересмотр цены 1» filter and the `price_variance_tolerance` setting both need
  it
- **`RateSnapshotRecord`** — persisted per ADR-0020; the menu's settings entry
  advertises `Снимок тарифов 8F41C2`. Nothing serves it
- **`Session`** — model only (§3.3)

### Fields served but not rendered

`PrinterView` already carries `printed_hours` (the kit's **Наработка** column,
absent from `FleetPage`), `amortization_per_hour`, `nominal_power_kw`, the build
volume, `nozzle_diameter_mm`, `services[]`, `slots[]`, `maintenance_due` — i.e.
the whole `spec`/`svc`/`ams` popup and the **Экономика** panel. `PrinterTable`
carries `attention`, which is the dashboard's counter.

`OrderView` carries `rate_snapshot_id` and `engine_version`, which the kit puts in
the chrome's meta strip and no screen reads.

### The live event stream is partly modelled

`api/ws.py` forwards `fleet.*`, `order.*`, `payment.settled` and `attention.*`.
The backend emits **21 event types**; `packages/events/src/types.ts` declared
**five**; exactly **one component subscribes** (`FleetPage`).

Forwarded to entitled clients with nothing modelling them:
`order.sla_credit_accrued` and `payment.settled`. Those are the real gap, and
closing them is cheap.

**Two corrections to an earlier draft of this section**, both found by reading
`ws.py` instead of inferring from the emitter list:

- `job.*`, `plate.*` and `printer.became_free` are **not** forwarded. They are
  emitted onto the in-process bus and stop there, because `LIVE_PATTERNS` does
  not carry them — so modelling them client-side would advertise a stream that
  never arrives. Putting the cabinet's pipeline on live `job.*` is a
  `LIVE_PATTERNS` change, not a wiring job, and deserves deciding on its own
  terms.
- **The cabinet cannot use this socket at all.** `_authenticate` requires
  `VIEW_PRODUCTION` (`backend/printorian/api/ws.py`), so a customer's handshake
  is closed with 4401. A live customer pipeline needs a *per-customer* channel:
  the hub fans one payload out to every subscriber with no per-actor filtering,
  so relaxing the gate would show each customer every order in the farm. New
  backend work, not §1.5.

Worth recording separately: `payment.settled` carries `amount`, and every
`VIEW_PRODUCTION` holder receives it — while `Permission` keeps `VIEW_FINANCIALS`
"deliberately separate from every production permission". The socket and the REST
API disagree about who may see money. It wants a decision rather than a patch.

`attention.*` is subscribed by the hub and **emitted by nothing** — a reserved
pattern the dashboard's «Требует внимания» panel is the natural first publisher
of.

### The generated client goes stale silently

`PromoPage` calls `/public/stats`, which was **absent** from the local
`packages/api-client/src/generated/schema.ts` — 46 paths against the server's 47.
The file is gitignored and rebuilt by `npm run generate:api`, so this is local
staleness rather than a committed lie. But ADR-0005 makes it the shared answer to
"does this endpoint exist?", and a stale local copy is exactly how a working
endpoint gets rebuilt from scratch. Regenerate before building against it.

### What this changes

A meaningful slice of §3 is frontend-only work misfiled as backend work. Before
starting any new context, the following are wiring jobs against a server that
already answers:

1. Regenerate the API client (unblocks everything below)
2. `STATUS ::` from `/health`; diagnostics rows from `/health/ready`
3. Configurator: scenario dialog on `/materials/recommend`; prepared-plate hint
   from `/jobs/plates/find`
4. Checkout: providers from `/payments/providers/available`
5. Fleet: the four-tab popup and the `Наработка` column — every field is served
6. Desk: `/orders/overdue` and `/jobs/wait-list` chips; the assignment-decision
   panel from `/jobs/{id}/decisions`
7. Broaden `packages/events` to all 21 emitted types and subscribe from the
   cabinet and the dashboard

Only the telemetry read API (§3.10) is a genuinely new endpoint among these, and
its storage is already partitioned and retained.

---

## 2. What the kit actually contains — the full inventory

This is the part the earlier documents skip. Every list, column, filter, sort key,
tab, control and action in the kit, with the backing it has today.

Legend: **✅** data exists · **◐** partially exists · **❌** nothing behind it.

### 2.1 promo.html — public

Hero with live proof numbers; itemised cost breakdown (`.hv-leader` rows into
`.hv-slab` totals); six feature cards; the 9-step flow (`.hv-pipe`); four
`.hv-bignum` proof figures; a 8-row *Printorian vs обычно* comparison table; four
catalogue teaser cards.

| Element | Backing |
|---|---|
| Proof figures (orders done, on-time %, models) | ✅ `GET /public/stats` |
| Breakdown demo rows | ✅ `pricing` |
| Catalogue teasers | ❌ needs the model library (§3.6) |

### 2.2 catalog.html — public · **not built**

The single densest screen in the kit.

- **Search** over `data-name` — name, code, purpose
- **Eight sort keys**, `data-sort-key`: `popular`, `price`, `time`, `volume`,
  `difficulty`, `rating`, `prints`, `date`. Cost-like keys open ascending,
  quality-like descending; clicking the active key flips it
- **Five facet groups**, `data-facet`: `cat` (case, decor, func, mech, org),
  `mat` (PLA, PETG, ASA, TPU, resin), `size` (s, m, l), `colors` (1, multi),
  `diff` (easy, mid, hard). **OR within a group, AND across groups**, and search,
  facets and sort run in **one pass** so sorting never clears a filter
- **Grid / list toggle** (`data-view-set`) — same cards, different class, scroll
  position preserved
- Per-card data attributes that must all become real fields: `data-popular`,
  `data-price`, `data-time`, `data-volume`, `data-difficulty` (0–10),
  `data-rating`, `data-prints`, `data-date`, `data-tags`
- **Model detail popup**: isometric/front/top/layers view switch, "Характеристики
  печати · ОЦЕНКА 0–10" spec bars, a **quantity ladder table** (Количество · За
  штуку · Итого · Срок), a **suitable-materials table** (Материал · Пригодность ·
  Δ цена · Наличие), "История модели · 312 ПЕЧАТЕЙ", download STL

| Element | Backing |
|---|---|
| Everything above | ❌ `contexts/catalog` is `ModelAsset` + `PreparedPlate` + `PlateLibrary`. There is no library of *catalogue* models — no categories, tags, difficulty, ratings, print counts |
| Quantity ladder | ✅ `pricing` can price it; needs a batch-quote endpoint |
| Δ price per material | ✅ `pricing/preview` |
| Measured time & price from the last real print | ◐ `PrintJob` + `EstimateVariance` hold the facts; nothing aggregates them per model |

### 2.3 configurator.html — public · built, restyle + two additions

Four numbered steps: `01 :: Материал` (СПОСОБ ВЫБОРА), `02 :: Цвет` (ДО 4 СЛОТОВ
AMS), `03 :: Размер и количество`, `04 :: Обработка и срок`.

| Element | Backing |
|---|---|
| Material by scenario **or** manual; alternatives popup with Прочность/УФ/Термо/Склад/Δ Цена | ◐ `GET /materials/recommend` exists; the **usage-scenario dialog is new UI** |
| Up to 4 AMS colour slots | ✅ |
| **Resize control** (`input[type=range]`, `data-suffix="%"`, `#scale-out`) | ◐ engine supports scale; **the control is not in the app** |
| Quantity ladder with per-step delta | ✅ `pricing/preview` |
| Post-processing: Шлифовка +340 ₽/шт · Грунтовка +610 ₽/шт · Окраска +1 250 ₽/шт | ✅ priced; ❌ no `postproduction` context to actually perform them |
| Rush: СРОК 18 Ч ВМЕСТО 74 Ч · +22% | ✅ |
| Itemised price with `.hv-leader__basis` under each line | ✅ `PriceBreakdown` |

### 2.4 checkout.html — public · built, restyle

`01 :: Учётная запись` (sign-in/register tabs) · `02 :: Доставка` (Курьер /
Самовывоз / Транспортная, city + address) · `03 :: Оплата` (radio group).
All ✅ except: the address fields are free text with **no saved-address picker**
(§3.5), and sign-in here should use the **popup** rather than a tab that can lose
the quote.

### 2.5 cabinet.html — public · stub

Order list (14) · **9-stage pipeline** `.hv-pipe` (ЭТАП 5 ИЗ 9) · Состав заказа ·
Оплата · **История заказа, 11 событий** (Время · Событие · Кто/что · Изменение) ·
Другие заказы · queue position · SLA credit.

| Element | Backing |
|---|---|
| Orders, pipeline, queue position, predicted start, SLA credit | ✅ `GET /orders/mine`, `/orders/{id}/queue` |
| Event history with actor and delta | ✅ `OrderEvent` |
| The rendering | ❌ `CabinetPage.tsx` is 27 lines and shows none of it |

Stage names come from `OrderStatus`: `awaiting_payment · prep · price_review ·
queued · printing · post_production · quality_check · packing · shipped`. The kit
counts nine and shows nine — but `post_production` and `quality_check` have no
context behind them, so two of the nine can never advance today (§3.7).

### 2.6 account.html — public · **not built**

Seven tabs (`data-tab-target`): `profile`, `orders`, `models`, `addr`, `pay`,
`notify`, `sec`. Identity plate, **tier ladder showing distance to the next
tier**, lifetime figures, activity over 12 months.

| Tab | Content | Backing |
|---|---|---|
| Профиль | Личные данные · IDENTITY.USER | ✅ |
| Заказы | Номер · Модель · Статус · Создан · Сумма; filters Все 14 / В работе 2 / Завершены 11 / Отменены 1; CSV export | ✅ |
| Мои модели | Uploaded `ModelAsset` cards with tags and re-order | ◐ entity exists, **no customer-scoped query** |
| Адреса доставки | Address list, one default, add/edit/delete | ❌ no `Address` entity |
| Оплата и документы | Saved cards (ЮKASSA), documents table (Документ · Заказ · Дата · Сумма), PDF | ❌ no saved methods, no invoice list |
| Уведомления | Per-channel × per-event switches, "Когда писать · ТОЛЬКО ПО ВАШИМ ЗАКАЗАМ", quiet hours | ❌ |
| Безопасность | Change password, **active sessions** (Устройство · Адрес · Последняя активность), end one / end all but current, export my data, delete account | ◐ `Session` is a model; `_revoke_all` is private and all-or-nothing |
| Tier ladder | Distance to Gold, not the badge held | ◐ `pricing.CustomerTier` has thresholds; **nothing exposes the running total or the gap** |

### 2.7 auth.html — public · **not built**

Three modes (`signin`, `signup`, `recover`) as a two-panel page **and** as a popup
on every screen. Password meter (`data-level`), inline field errors
(`data-invalid`), "Войти по коду из письма", six-digit OTP input (`.hv-otp`).

| Element | Backing |
|---|---|
| Sign in, register | ✅ |
| **Recovery by six-digit code** | ❌ no `otp`, `reset_code` or `recovery` anywhere in the backend |
| The popup | ❌ |

### 2.8 blog.html / blog-post.html — public · **not built**

Index: featured report, category filter (`all`, `cost 5`, `arch 3`, `materials 4`,
`fleet 4`, `post 2`), 7 post cards, archive with dotted leaders (ВЫПУСКИ 34–50),
search, subscribe by email, RSS.
Post: contents sidebar (7 entries) with scroll-spy, reading progress bar, key
figures, right rail (Другие отчёты · Проверить на себе · Упомянуто в отчёте),
callouts, tables, share, PDF.

**❌ No backend at all.** Independent of everything else.

### 2.9 dashboard.html — control · **not built**

The kit's most demanding screen. Four questions, four shapes.

| Panel | What it needs |
|---|---|
| **KPI tiles** (11) — Новых сегодня · За месяц · В работе сейчас · Средний чек · Загрузка парка · Эффективность · Наработка за сутки · Простой · Поступило · Расходы · Прибыль | ❌ period-over-period aggregation with a delta and a **sentiment** (`data-sentiment` — spend rising is red, revenue rising is green) |
| **Status wall** — one `.hv-node` per printer, grouped by shop zone, glow by state, breathing when printing, blinking on error | ✅ `fleet.*` events serve state; `Printer.location` groups it. Frontend-only work |
| **12-hour schedule** — `.hv-gantt` rows with a live `now` line, bars keyed `printing`/`queued`/`service`/`blocked` | ❌ needs per-printer ETA + queue depth on a time axis |
| **Filament headroom** — `.hv-stack-bar` per material, parts `loaded` / `stock` / **`committed`** | ◐ loaded and stock ✅; **committed** means summing `grams_required` across planned jobs per material — the number nobody tracks and the one that causes the stall |
| **Stage funnel** — 9 rows, count per stage | ◐ 7 of 9 countable; `post_production` and `quality_check` need §3.7 |
| **Куда ушли деньги** — 6 rows: Материал · Труд · Амортизация · Логистика · Накладные · Брак и возвраты, 781 ТЫС ₽ | ❌ P&L by category over a period |
| **Загрузка по часам** — `.hv-heat`, 7 × 24 grid, 168 cells | ❌ |
| **Требует внимания** — 4 alert rows | ❌ alert policy: which conditions, repeats suppressed |
| **Выручка по дням · 30 суток** sparkline | ❌ |
| **Printer popup** — current task + progress + customer, live telemetry (Сопло/Стол/Камера/Скорость), AMS slots (Слот · Материал · Партия · Остаток), machine economics, that machine's own queue (Заказ · Модель · Материал · Начало · Длительность), actions Пауза/Остановить/Открыть/Проверить | ◐ telemetry and AMS ✅; per-machine economics and queue ❌ |

### 2.10 orders.html — control · built, restyle + one addition

Table: Номер · Заказчик · Модель · Статус · Принтер · Обещанный срок · Сумма ·
К оплате. Filters: Все 18 / Ожидает оплаты 2 / Пересмотр цены 1 / В очереди 3 /
Печатается 7 / Постобработка 4 / Отправлен 1. Detail: Перевести в статус · Вернуть
деньги (reason required) · Платежи (Время · Провайдер · Тип · Статус · Сумма) ·
**Себестоимость и маржа · ТОЛЬКО ДЛЯ УПРАВЛЕНИЯ**.

All ✅ except **per-order margin** — derive from the pinned `Breakdown` at read
time; do not store a second copy that can disagree with the snapshot it came from.
Gate on `VIEW_FINANCIALS`.

### 2.11 fleet.html — control · built, restyle + detail popup

State counters (Все 12 / Печатает 7 / Свободен 2 / Обслуживание 1 / Ошибка 1 /
Не в сети 1). Sortable table: Принтер · Состояние · Задание · Прогресс · Готово к ·
На связи · Расположение · Наработка. Detail popup with **four tabs**: `now`
(current job, telemetry), `spec` (Характеристики), `ams` (slots), `svc` (service
card: Операция · Периодичность · Выполнено · Осталось; Экономика).

✅ throughout, including `Расположение` (`Printer.location`, already rendered) —
`ServiceOperation` carries `kind`, `interval_hours` and `last_done_at_hours`, and
`maintenance_due` derives from them. Missing on the frontend only: the **four-tab
detail popup**, and the `Наработка` column, which `PrinterView.printed_hours`
already serves.

### 2.12 materials.html — control · built, restyle

Family glyphs (PLA/PETG/ASA/TPU with density and stock). Status counters (Все 24 /
На складе 14 / В принтере 6 / Заказан 2 / Нет в наличии 2). Table: Код · Название ·
Семейство · Статус · Остаток · Расположение · Закупка / 1000 м · Цена / см³.
Detail: Свойства · Партии и размещение (Партия · Где · Остаток) · Цена ·
Совместимые принтеры 7 из 12 · actions (Заказать ещё · Списать · История закупок ·
История расхода · Расход по заказам).

✅ except: *История закупок* needs purchasing (§3.9), *Расход по заказам* needs a
consumption ledger, *Совместимые принтеры* needs the capability match exposed.

### 2.13 users.html — control · built, restyle + sessions

Role counters (Все 34 / Владелец 1 / Менеджер 1 / Инженер 2 / Оператор 3 /
Заказчик 27). Table: Почта · Роль · Статус · Последний вход · Заказов. Right rail:
**Права роли · 14 ИЗ 14** permission matrix, **Активные сессии · 2**. Actions:
Пригласить · Сменить роль · Отключить · Завершить (one session).

| Element | Backing |
|---|---|
| Users, roles, permission matrix (18 permissions in `Permission`) | ✅ |
| Заказов per user | ◐ derivable, not exposed |
| **Session listing** `GET /users/{id}/sessions` | ❌ |
| **Single revocation** `DELETE /sessions/{id}` | ❌ |
| Пригласить (invite flow) | ❌ — today staff accounts are created directly |

### 2.14 settings.html — control · **not built** · 15 sections, 100 parameters

The kit's identifiers are the real ones. Verbatim inventory:

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
| **Доступ и безопасность** | `session_ttl_hours` `password_min_length` `password_hasher` `require_2fa_for_management` `lockout_attempts` `audit_retention_days`; permission matrix; API keys table (Название · Префикс · Права · Создан · Использован) |
| **Интеграции** | `slicer_engine` `slicer_path` `slicer_profile` `slicer_timeout_seconds` `bambu_connection` `bambu_cloud_account` `bambu_transport`; webhooks table (Событие · URL · Состояние) |
| **Диагностика** | Read-only: 12 `.hv-health` subsystem checks with latency, versions, last log lines. **Nothing here is a setting** |
| **Обслуживание системы** | `backup_enabled` `backup_hour` `backup_retention` `backup_path` `model_retention_days` `telemetry_retention_days` `maintenance_mode`; **change audit log** (Время · Кто · Параметр · Было · Стало) |

Interaction the screen requires: editing a row **marks it dirty**, reveals the
previous value (`.hv-set__was`, "БЫЛО 9"), offers a per-row revert, and counts
into a save bar (`data-dirty`). Secrets are write-only — `yookassa_secret_key`
shows "КЛЮЧ СОХРАНЁН · Заменить" and can never be read back.

**❌ All 100 are constants in code today.**

### 2.15 postproduction.html — control · **not built**

- **Task board** — `.hv-task` cards ordered by urgency, priority stripe
  (`data-pri`: `rush`/`soon`/`normal`/`live`), countdown against the promise
  (`.hv-due`), state `late`/`ok`/`soon`
- **Instruction** — numbered `.hv-step` list, **time norm per step**, done marks,
  a warning block for the thing that actually causes returns
- **Scorecard** — Операции за 30 дней: Операция · Сделано · Норма · Факт · Темп ·
  Возвраты
- **Смены** — output per day (`.hv-col` bars)
- **Badges** — `.hv-badge` with `data-tier` 0–3, monochrome, unearned shown dimmed,
  all accrued from recorded facts and none awardable by hand
- Actions: Отметить шаг выполненным · Пауза · Сообщить о браке

**❌ `contexts/postproduction` does not exist.** This blocks the cabinet's stages
6–7 and the dashboard funnel.

### 2.16 packaging.html — control · **not built**

- **Cut-off clock** — ДО ОТСЕЧКИ 2 Ч 14 М, drives the queue order
- Queue cards with tare and weight, "МАРКИРОВАНО" state
- **Completeness check** — СВЕРИТЬ ДО ЗАКРЫТИЯ КОРОБКИ, Позиция · Заказано ·
  В наличии, with a Расхождение action
- **Tare table** — Тип · Габарит · Цена · Расход/мес · Остаток · **Хватит на**
- Tare auto-selection by dimensions ("ПОДОБРАНО СИСТЕМОЙ")
- Instruction (ШАГ 2 ИЗ 5), post marks, operator badges, 30-day KPIs
- Actions: Печать этикетки · Печать паспорта · Отметить шаг

**❌ Nothing.** Depends on §3.7 for the QC → packing handover and on the tare
catalogue in settings.

### 2.17 service.html — control · **not built**

Five ticket kinds: **установка · ремонт · ТО · загрузка материала · перемещение**.

- Ticket board with priority and elapsed time; "СООБЩИЛ ДРАЙВЕР" origin
- **Последствия** — what this ticket already costs
- **Порядок работ** — steps with norms
- Fleet reliability table: Машина · Состояние · Наработка · Ближайшее ТО ·
  Отказов/год · Надёжность
- **Причины отказов** — 90 суток · 18 случаев, funnel
- **Запчасти на посту** — Позиция · Остаток · Расход/мес · Статус
- Crew badges and marks; MTTR, fleet readiness
- Actions: Создать заявку · Передать инженеру · Списать запчасть · Вывести машину
  из работы · Отметить шаг

| Element | Backing |
|---|---|
| `ServiceOperation` with kind/interval/hours | ✅ |
| Наработка, Ближайшее ТО | ✅ |
| **Tickets** as an entity, with steps, assignee, elapsed, consequence | ❌ |
| Failure causes, MTTR, отказов/год, надёжность | ❌ no failure record |
| Spare parts stock | ❌ inventory only knows filament |

### 2.18 purchasing.html — control · **not built**

- **Структура закупок** — 412 ТЫС ₽ за месяц, funnel by class
- **Требуют заказа сейчас** — Позиция · Класс · Остаток · **Последствие**, by
  reorder threshold
- **Purchase orders** — Номер · Поставщик · Состав · Статус · Заказан · Ожидается ·
  Сумма; filters Черновик 1 / Отправлен 1 / В пути 4 / Принят 1
- **Supplier scorecards** — Поставщик · Поставок · В срок · Брак · Оборот ·
  **Оценка** over full history (the kit shows 3D-Партс sliding to 6.2)
- **Цены по ключевым позициям** — a year of price history
- PO detail: 6-stage path, line items, **Зачем этот заказ** justification,
  **Приёмка** (receiving into lots)
- Actions: Новый заказ · Отправить · Отследить · Начать приёмку · Претензия ·
  Скачать счёт · Сравнить с альтернативой

**❌ Nothing.** Four purchasable classes: materials, spare parts, packaging,
printers.

### 2.19 store.html — control · **not built**

- **Cell map** by zone — `.hv-node` per cell across zones A/B/C, brightness =
  fill; 20 named cells in the kit (A1-1 PETG-CF … C7-1 СТОЛЫ)
- **Movements today** — Время · Операция · Позиция · Откуда → куда · Кол-во ·
  Основание
- **Batches in a cell** — FIFO, oldest first: Партия · Принята · **Сушка** ·
  Остаток · Статус
- **Turnover** — days on shelf per class; **dead stock** with the money in it
- **Stocktake** — Позиция · Ячейка · Лежит · Стоимость, last count date
- Actions: Принять партию · Переместить · Выдать в производство · Отправить на
  сушку · Списать · Начать инвентаризацию · Пересчитать

| Element | Backing |
|---|---|
| `MaterialLot` with location | ◐ location exists; **cells and zones are not a model** |
| Drying state, `require_drying`, `drying_valid_hours` | ❌ |
| Movement ledger with reason | ❌ |
| Turnover, dead stock, stocktake | ❌ |

### 2.20 logistics.html — control · **not built**

- **Отгрузка сегодня** to the same cut-off as packaging
- **Carriers** — Перевозчик · Отправлений · В срок · Повреждений · Средняя цена ·
  **Оценка**
- **Зоны и тарифы** — Зона · Отправлений · Базовая · За кг · Срок; *these land in
  the order's estimate*, so they are the same rows as the settings zones table
- **Сроки доставки** — Зона · Обещано · Факт · **Точность** (Урал at 78%)
- **Возвраты** — 30 дней · 1.6%
- **География за месяц** — 248 отправлений
- Shipment detail: 6-stage path, address from the cabinet, delivery calculation,
  tracking event history
- Actions: Печать этикетки · Сформировать реестр · Сменить перевозчика ·
  Перенести на завтра · Отменить отправку · Претензия перевозчику · Перепечатать
  заказ

**❌ Nothing.** No `Shipment`, no carrier, no zone, no tracking.

### 2.21 Cross-screen conventions the app must honour

| Convention | Where it appears | State |
|---|---|---|
| `data-realm` public/control split, hazard rail, realm badge | every screen | ❌ |
| `data-theme` Void/Paper | every screen | ✅ `ThemeSwitch` |
| `RU`/`EN` in the OS bar | every screen | ◐ wrong place |
| `data-tabs` / `data-tab-target` / `data-tab-panel` | account, auth, fleet, settings, checkout | ❌ CSS section 22 not ported |
| `data-filter` / `data-filter-group` counter chips | account, blog, fleet, materials, orders, purchasing, users | ◐ ad-hoc per screen |
| `data-sort-value` sortable headers | fleet, materials, orders, purchasing, service, store, logistics | ✅ `DataTable` |
| `data-open` modal targets | 11 screens | ❌ no shared modal |
| `data-state` machine states (`printing`/`idle`/`error`/`offline`/`maintenance`/`paused`/`preparing`/`finished`) | everywhere | ✅ |
| `data-tone` (`live`/`good`/`warn`/`bad`) — colour means machine state, never decoration | everywhere | ✅ |
| `data-pri` (`rush`/`soon`/`normal`/`live`) task priority | postproduction, packaging, service, logistics | ❌ |
| `data-tier` 0–3 operator badges | postproduction, packaging, service | ❌ |
| `data-sentiment` (spend up = bad, revenue up = good) | dashboard, logistics, purchasing, store | ❌ |
| `data-bind` live numeric readouts | configurator, settings, index | ◐ React owns this |
| `data-auth-open` popup on any element | every public screen | ❌ |
| Tabular figures, right-aligned, basis set underneath | everywhere | ✅ |

---

## 3. What has to be built

Backend first, because thirteen of the twenty-one screens have no server behind
them. Sized relative to each other, not in hours.

### 3.1 The shell and the realm split — frontend only · small

1. Set `data-realm` on `<html>` from the app (`public` in web, `control` in
   console), and port `design/css/realm.css` into
   `packages/ui/src/harvester/realm.css`.
2. Port CSS section 22 (`[data-tab-panel]`) — five screens need it.
3. Import `screens.css` from `index.css`. It is finished and unused.
4. Move `RU`/`EN` into the OS bar; add the realm badge that opens the menu
   filtered to the other side.
5. `STATUS ::` from real health, not a literal.

**Exit:** a control screen shows the hazard rail and graph ground, a public screen
does not, and no screen imports a stylesheet it cannot see.

### 3.2 The navigation overlay — frontend only · small

Extend `NavOverlay` to the kit's contract: territory sections with the access
border, three realm filters with counts, per-territory numbering, flag chips,
locked rows from `actor.permissions`, and the preview pane (mark, kicker, prose,
three stats, one of seven schematic shapes). The `ROUTES` table in
`design/js/menu.js` is the source — it was written as one table precisely so this
could be one component.

**Exit:** the overlay lists all 21 destinations, dims what the actor cannot reach
rather than hiding it, and crossing realms requires a deliberate act.

### 3.3 Auth: popup, recovery, sessions — small

- `AuthDialog` wrapping `AuthPanel`, opened by any `data-auth-open` trigger,
  used at checkout so a configured quote survives signing in
- Recovery by **six-digit code**: a short-lived entity (user, six digits, issued,
  expires, attempt counter); `POST /auth/recover` answering identically whether or
  not the address is known — a differing answer is an account-existence oracle;
  `POST /auth/recover/verify` → one-use token; `POST /auth/recover/reset`.
  **The code-not-link choice belongs in an ADR before it is built.**
- `GET /users/{id}/sessions` (id, user agent, created, last seen, expires) and
  `DELETE /sessions/{id}`. `_revoke_all` stays for deactivation; single revocation
  is new. Session count on the users table.

**Exit:** a customer recovers a password from a code, sees their sessions and ends
one; a manager ends someone else's.

### 3.4 The settings store — medium, high leverage

Everything downstream reads from it, so it goes early.

- Typed table: key, value, type, default, section, updated_by, updated_at.
  **Reads fall back to the code default**, so an empty table behaves exactly as
  today and the migration is a no-op
- `RateSnapshot`, `SchedulingPolicy` and `core.config` read through it —
  **resolving once at the edge and passing in**, never fetching inside the engine.
  ADR-0002 forbids the alternative and `import-linter` will fail the build,
  correctly
- Secrets stored encrypted and **write-only** at the API boundary
- Change audit log retained for `audit_retention_days`
- Endpoints: `GET /settings` (grouped, with defaults and dirty state),
  `PATCH /settings` (batch, atomic), `GET /settings/audit`,
  `GET /diagnostics` (read-only subsystem health)
- Ends the `material_procurement_flat = 500 ₽` placeholder

**Exit:** an owner changes `margin_percent` in the UI, the next quote uses it, and
the old value, the actor and the time are recorded.

### 3.5 The customer's own record — medium

- `Address` per customer, one default; used by checkout and by logistics
- Saved payment methods — **provider token only, never a PAN**
- Notification preferences per channel × per event, plus quiet hours
- Customer-scoped `ModelAsset` query
- Tier ladder: expose the thresholds **and the customer's running total**, so the
  screen can show the gap rather than the badge

**Exit:** a customer places an order to a saved address and sees how far they are
from the next tier.

### 3.6 The model catalogue — large · depends on completed jobs

- `CatalogModel`: category, tags, difficulty 0–10, rating, print count,
  publication state, preview geometry, linked `ModelAsset`
- One query pass: search + facets (OR within, AND across) + sort over eight keys
- **Measured** print time and price from the last real print — the page's whole
  claim. Until one exists, show the estimate **labelled as an estimate**;
  presenting one as the other is ADR-0007's defect in a new place
- Batch quote for the quantity ladder; per-material Δ for the suitability table

**Exit:** a customer finds a model by facet, sees what it actually cost and took
last time, and configures it in two clicks.

**Status: built**, except the last clause — there is no configure-from-catalogue
handoff yet, so a reader still returns to the configurator and uploads. The rest
is done and verified in the browser: OR-within-group widens (PLA 4 → PLA∪ASA 5),
AND-across-groups narrows, sorting does not clear the filter, all four cost-like
keys open ascending and all four quality-like descending, Cyrillic search matches
case-insensitively through the folded `search_text` column, and a model nobody has
printed reads `НЕ ПЕЧАТАЛАСЬ` / `ЦЕНА ПО РАСЧЁТУ` rather than a fabricated figure.

**The model popup is synced to the kit**, with a real 3D view: three.js bundled
locally (never a CDN — the farm's LAN resolves nothing external, ADR-0003),
rendering the actual STL through `GET /catalog/{slug}/model`. Flat unlit surface
with its edges drawn on top, in the theme's own tokens, so it reads as an
engineering view rather than a product render. Iso/top/front presets, orbit, zoom,
optional turntable, and an STL download.

Four panels now carry what the kit shows: measured print facts, the six 0–10 spec
bars, geometry measured at upload (volume, bounding box, surface area, triangles,
watertightness, mesh warnings) and model history.

**Two panels are deliberately absent**: the quantity ladder and the per-material
Δ price. Both are pricing questions, and the honest answer is a quote from the
pricing engine rather than a table this screen invented. They need a batch-quote
endpoint, which is the next piece of this slice.

**One deliberate departure.** The slice says to show the estimate *labelled as an
estimate* until a real print exists. The screen shows **no number at all** in that
case, because the catalogue API returns measurements and does not compute
estimates — and inventing one in the client, beside a row that is a genuine
measurement, is the confusion the rule exists to prevent. The detail panel says so
in words and points at the configurator, which is where a real quote comes from.

### 3.7 Post-production, QC and packaging — large · unblocks four screens

`contexts/postproduction` and `contexts/packaging`. The largest single item, and
the one most other things wait on: it owns two of the cabinet's nine stages, two
rows of the dashboard funnel, and the whole shop-floor trio.

- Operation catalogue with **norm-hours per step** (base + per cm² of surface),
  from settings
- Tasks with priority, promise countdown, assignee, step marks, timers
- Consumables per operation; photos; QC verdict and return reasons
- Packaging: cut-off clock, tare catalogue and auto-selection by dimensions,
  completeness check against order lines, label and passport printing
- **Operator badges and scorecards accrued from recorded facts only** — the kit is
  explicit that none can be awarded by hand, which makes this a derived read, not
  an editable table

**Exit:** an order walks from `printing` through `post_production`,
`quality_check` and `packing` to `shipped` with an operator's marks and times
behind every stage, and the funnel counts nine real stages.

### 3.8 Service tickets — medium · depends on nothing

Five kinds (установка, ремонт, ТО, загрузка, перемещение), steps with norms,
assignee, elapsed, and the **consequence** — what this ticket already costs.
Failure records behind отказов/год, надёжность and MTTR. Spare-part stock, which
inventory does not model today.

**Exit:** a driver-reported fault opens a ticket by itself, an engineer works it
to a norm, and the fleet reliability table is computed from closed tickets.

### 3.9 Purchasing, warehouse, logistics — large · one chain

The kit treats these as one chain and so should the build.

- **Purchasing**: suppliers with accumulated scores (punctuality, defect rate,
  price over full history), purchase orders with a 6-stage path, reorder points
  driving *Требуют заказа сейчас*, receiving into lots, price history
- **Warehouse**: zones and cells as first-class, batch FIFO, drying state and
  validity, a movement ledger with a reason on every row, turnover and dead stock,
  stocktake
- **Logistics**: `Shipment`, carriers with scores, zones and tariffs — *the same
  rows the settings screen edits and the pricing engine already reads for
  delivery* — tracking events, returns, promised-vs-actual accuracy per zone

**Exit:** filament arrives on a purchase order, is received into a cell, dried,
issued to a printer, and the finished order ships on a carrier — with every step
recorded and no number typed twice.

### 3.10 The dashboard aggregation layer — medium · richer after 3.7 and 3.9

KPI tiles with period-over-period deltas and sentiment; schedule from ETA and
queue depth; filament headroom including **committed grams**; stage funnel; spend
by category; the 7×24 heat grid; the alert policy (which conditions, and repeats
suppressed for five minutes — that is policy, not platform, per ADR-0016).

Served as one `GET /dashboard?period=` rather than eleven calls, since every tile
needs the same two windows.

**Exit:** an operator across the room can tell which machine needs attention, and
the owner can see where the month's money went.

### 3.11 The journal — small · independent

`Post`: slug, title, category, body, contents, publication state, published_at.
Public list and detail, author-only drafts. Contents sidebar and reading progress
are rendered from the body's headings — a client concern, not a field.

**Exit:** an editor publishes a report and it appears with its archive entry.

### 3.12 The restyle — frontend only · runs alongside everything

Convert `apps/web/src/app.css` (119 refs) and `apps/console/src/console.css`
(131 refs) off `--pr-*`. Delete Slice B's aliases and the bare-control fallbacks
in `tokens.css` when the last one goes. `design/js/kit.js` is deleted on contact —
React owns tabs, sorting, filtering, modals and the clock.

Two shared components fall out of this and should be built once: a **modal** for
the eleven `data-open` targets, and a **filter chip bar** for the seven screens
with `data-filter-group`.

**Exit:** `grep -ro -- --pr- frontend --include=*.css | wc -l` returns 0.

---

## 4. Sequencing

```
3.1 shell ──┬─→ 3.2 overlay
            └─→ 3.12 restyle ──→ every screen below renders correctly

3.4 settings ──┬─→ 3.7 (norms, QC gate, tare)
               ├─→ 3.9 (thresholds, zones, lead days)
               └─→ 3.10 (nothing hardcoded on the dashboard)

3.3 auth ──→ 3.5 account
3.6 catalogue  needs completed jobs before its central claim is true
3.7 ──→ cabinet stages 6–7, dashboard funnel, packaging, postproduction
3.8, 3.9, 3.11  independent
3.9 ──→ 3.10's "куда ушли деньги"
```

Four things can run at once from a standing start: **3.1+3.2** (frontend shell),
**3.4** (settings), **3.3** (auth), **3.11** (journal). None of them touch the
same files.

**3.1 before 3.12 is not negotiable** — restyling a screen before the realm split
and `screens.css` are in place is the same work twice.

**3.4 before 3.7 and 3.9.** Building post-production with hardcoded norms, or
purchasing with hardcoded thresholds, means retrofitting the settings read into
both later.

**§1.5 comes before all of it.** Seven wiring jobs against endpoints the server
already answers, plus one client regeneration. They cost days, not weeks, and two
of them (the assignment-decision panel, the scenario dialog) close items §3 would
otherwise schedule as new work.

Suggested order if run serially:
`1.5 → 3.1 · 3.2 · 3.12 → 3.4 → 3.3 · 3.5 → 3.7 → 3.8 · 3.9 → 3.10 → 3.6 → 3.11`

---

## 5. Files this creates

```
frontend/packages/ui/src/
  harvester/realm.css           new — the public/control split
  harvester/tabs.css            new — CSS section 22
  harvester/index.css           edit — import screens.css, realm.css, tabs.css
  shell/AppShell.tsx            edit — data-realm, realm badge, lang in OS bar
  shell/RealmBadge.tsx          new
  nav/NavOverlay.tsx            edit — territories, filters, preview pane
  overlay/Modal.tsx             new — the eleven data-open targets
  filters/FilterBar.tsx         new — the seven data-filter-group screens
  session/AuthDialog.tsx        new — the popup
  settings/SettingRow.tsx       new — name, code, unit, was-value, revert
  board/TaskCard.tsx            new — shop-floor task board
  board/Instruction.tsx         new — numbered steps with norms
  board/Badges.tsx              new — data-tier 0–3
  charts/{StatusWall,Gantt,Funnel,StackBar,Heat,Sparkline}.tsx   new

frontend/apps/web/src/
  CatalogPage.tsx  AccountPage.tsx  AuthPage.tsx  BlogPage.tsx  BlogPostPage.tsx
  CabinetPage.tsx               rewrite — 27 lines is a stub

frontend/apps/console/src/
  DashboardPage.tsx  SettingsPage.tsx  PostProductionPage.tsx  PackagingPage.tsx
  ServicePage.tsx  PurchasingPage.tsx  StorePage.tsx  LogisticsPage.tsx

backend/printorian/contexts/
  settings/       postproduction/    packaging/
  service/        procurement/       warehouse/
  logistics/      journal/           analytics/
  catalog/library — extend to a real model catalogue
```

`PrepPage.tsx` has no kit screen. Either it gets one drawn or it folds into the
order desk; leaving it as the one screen speaking a private visual language is the
outcome to avoid.

---

## 6. Risks

| Risk | Why it is real | Mitigation |
|---|---|---|
| **The kit is treated as thirteen new screens rather than eight new subsystems** | Twelve of the twenty-one screens are views onto contexts that do not exist. Building the React first produces thirteen mock-data pages that then have to be rewritten | Backend slice before its screen, every time. §3 is ordered that way deliberately |
| **Settings breaks pricing purity** | A settings read inside the engine makes pricing depend on I/O and voids ADR-0002 | Resolve the snapshot once at the edge; `import-linter` already fails the build otherwise |
| **The catalogue's "measured" claim is unfounded early** | With few completed jobs there is nothing to measure, and falling back silently to estimates is ADR-0007's defect wearing a different hat | Label the estimate as an estimate until a real print exists. Never present one as the other |
| **The Harvester migration stalls half-done** | Two token sets shipping at once is how a UI ends up permanently inconsistent. 250 `--pr-*` refs is where it currently sits | Treat §3.12 as one unit with a countable exit. The number only goes down |
| **`screens.css` rots while unimported** | 1 610 lines nothing exercises will drift from the kit it was copied from | Import it in §3.1, before any screen needs it |
| **Realm leakage** | A control screen bundled into the storefront ships margin figures and printer credentials to anonymous visitors | Keep the two apps split (ADR-0016); the routing table in §0 is the check |
| **The shop-floor trio is built three times** | Post-production, packaging and service answer the same three questions in the same order and share one vocabulary | Build `TaskCard`, `Instruction` and `Badges` once in `packages/ui`, before the first of the three screens |
| **Operator badges become political** | A badge that can be awarded by hand stops measuring anything | Derived reads only, from recorded facts — as the kit states |
| **Working backend is rebuilt because nobody knew it was there** | 22 of 46 endpoints have no consumer and the generated client is already behind the server, so "does this exist?" currently gets answered from the client rather than the API | §1.5 first, and regenerate the client as its opening step. ADR-0005 makes that file the shared answer — a stale one makes the whole team guess |

---

## 7. Out of scope

The kit shows no screen for it and the roadmap excludes it: resin workflow,
multi-tenant, multi-site, native mobile, in-app slicing, AI failure detection,
messenger bots beyond the Telegram notification channel, runtime plugins. Each is
reopenable with an ADR.

Two known holes the kit itself carries, worth recording rather than discovering:

- **`index.html`** is the component reference. It is not a product screen and does
  not get built; it is the acceptance target for `packages/ui`.
- **The kit's own faces cannot render Russian.** Already corrected in Slice B —
  each role is two faces split by `unicode-range` (Orbitron + Play, Chakra Petch +
  Exo 2, Share Tech Mono + JetBrains Mono). Any new face added later must be
  paired the same way or the tracked console character never reaches the words.

---

## 8. What has landed

Verified against a running dev server, not asserted. Everything here passes
`npm run typecheck`, `npm run lint`, `npm test` (117) and the backend's
`test_events_ws.py`.

### §1.5 — wiring against a server that already answers

| Item | State |
|---|---|
| Regenerate the API client | **Done.** 46 → 47 paths; `/public/stats` present |
| `STATUS ::` from `/health/ready` | **Done.** `useHealth`, 30s heartbeat, four states — `PROBING` is distinct from `OFFLINE` so the strip is never wrong during the first 200ms |
| Broaden `packages/events` | **Done**, with two corrections to §1.5's own claims. Adds `order.sla_credit_accrued` and `payment.settled`; deliberately does *not* add `job.*`/`plate.*`, which the socket never forwards |
| `Наработка` column | **Done.** `PrinterView.printed_hours` was already served; sorts numerically, since the wire carries a decimal string |
| Configurator scenario dialog · prepared-plate hint | Not started |
| Checkout payment providers | Not started |
| Fleet four-tab popup | Not started |
| Desk overdue / wait-list chips · decisions panel | Not started |

**Both directions of the event contract are now pinned by a test**, and both were
checked by breaking them:

- `packages/events/src/types.test.ts` — models nothing the socket cannot send
- `tests/api/test_events_ws.py::test_every_forwarded_event_is_modelled_by_the_client`
  — every exactly-named forwarded event is modelled client-side

The second reads `types.ts` from Python, because only the backend side can see
both files: the client package targets the browser and should not acquire a
filesystem dependency to check a contract. Wildcards are asserted in one
direction only — `order.*` legitimately covers names no client need narrow.

### §3.1 — the shell and the realm split · done

- `design/css/realm.css` → `packages/ui/src/harvester/realm.css`; kit section 22
  → `tabs.css`; both plus `screens.css` now imported by `index.css`. The
  redundant `./harvester-screens.css` export is gone, so nothing can double-load
- `data-realm` on `<html>`, applied in `main.tsx` **before first paint** as well
  as by the shell. The console's sign-in door is drawn outside `AppShell`, and a
  door into the пульт without the hazard rail is the one screen where the signal
  would be missing at the moment someone is deciding whether they are in the
  right place
- `RU`/`EN` moved into the OS bar, matching the kit exactly: it is a property of
  the console, not of the section
- The realm badge, opening the overlay filtered to the *other* territory

All four channels of the split verified in the browser:

| Channel | public | control |
|---|---|---|
| Texture | no rail | rail `14px`, `position: fixed`, hatched, vertical legend |
| Ground | `background-image: none` | graph paper |
| Density | appbar padding 12px, brand 18px | compact |
| Badge | `Витрина`, solid flag | `Пульт`, hatched flag |

### §3.2 — the navigation overlay · done

Territories, the hatched `ГРАНИЦА ДОСТУПА` border, three realm filters with live
counts, per-territory numbering, flag chips, locked rows, and the preview pane
with its seven schematic route markers.

Verified in the storefront with a console URL configured: filters read
`Всё 4 · Витрина 3 · Пульт 1`, numbering restarts (`01 02 03` then `01`), the
border appears only *between* the two territories, and the preview renders its
mark, path strip, prose and three-stroke drawing.

**The open sequence, including the parts CSS cannot express.** `menu.css` was
ported byte-identical, so the backdrop scale, corner brackets, scan pass, 34ms
entry stagger (`--i`), preview flicker and route-diagram draw-in are all
keyframes. Three pieces are script, and were missing until they were looked for:

- **The decode effect** — the active label resolving out of noise at ~30fps.
  This has to be JavaScript: the characters are *different characters* frame to
  frame, not a transform of the same ones. Measured over a 300ms window: 9 of 10
  frames noise with motion allowed, **0 of 10 under `prefers-reduced-motion`**,
  where the final text renders on the first frame rather than being slowed down.
- **`data-swapping`** on the preview, a timed flag rather than a transition
  because the flicker marks *replaced content* — there is no property
  interpolating between the old route and the new one. Verified cycling
  `false → true → false` across a row change.
- **The SVG's presentation attributes.** `fill="none" stroke="currentColor"
  stroke-width="1.5"` live in the kit's *markup*, not in `menu.css`. Without them
  the route diagrams paint as solid fills and `hv-draw` has no stroke to draw —
  a visible defect, not missing flavour.

The accessible name never scrambles: the noise is `aria-hidden` and each row
carries a stable `aria-label`, so a screen reader announces the destination
throughout instead of four hundred milliseconds of Cyrillic garbage.

**Crossing realms is a page load, not a state change.** `NavRoute.href` carries
destinations in the other bundle, because ADR-0016 puts them on separate origins
and `onNavigate` has no key for a screen this bundle does not contain. Cross-realm
entries appear only when `VITE_CONSOLE_URL` / `VITE_STOREFRONT_URL` is set —
advertising a URL that does not resolve is worse than listing nothing — and never
in the masthead, because the kit is explicit that the app bars do not cross.

### Still ahead

Everything in §3.3 through §3.12, four items of §1.5, and the twelve screens that
need a backend context before they need a React file. The `--pr-*` count is
unchanged at 250: §3.12 has not started, and the number is meant to be the honest
meter.
