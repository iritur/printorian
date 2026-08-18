# Printorian — visual language ("Harvester")

Static HTML + CSS templates for review. Nothing here is wired to the backend; the
point is to agree on the look before it goes into `frontend/apps/web`.

Open [`index.html`](index.html) — it is the component reference and links to every screen.

Double-click [`run_design.bat`](../run_design.bat) in the repository root, or serve it
by hand **from the repository root** — the path is relative, so running this from
inside `design/` looks for `design/design` and answers 404 to everything:

```bash
npx --yes http-server design -p 4180 -c-1
```

## Files

| File | What it is |
|---|---|
| `css/tokens.css` | Every colour, metric and type decision. Two themes: **Void** (black, default) and **Paper** (light, from the printed-dossier sketch). |
| `css/harvester.css` | The component system, in 22 numbered sections. |
| `css/menu.css` | The full-screen navigation overlay and its animation sequence. |
| `css/realm.css` | The public / control split. Loaded last so it can override the base components. |
| `js/menu.js` | The overlay's behaviour and its route table. |
| `js/auth.js` | The sign-in / register / recover popup, injected on every page. |
| `js/kit.js` | Demo behaviour only — tabs, sorting, filtering, modals, the clock. **Deleted on integration**; React owns this state. |
| `*.html` | Twenty-one screens plus the kit. |

## Screens

| File | Screen | Scenario coverage |
|---|---|---|
| `promo.html` | **Main / promo page** | The one outward-facing screen: hero, real breakdown, six features, the 9-step flow, proof numbers, us-vs-typical |
| `dashboard.html` | Farm overview | Orders / printers / finance KPIs, the status wall, schedule, filament headroom |
| `catalog.html` | Model library | Search, 8 sort keys, faceted filters, grid/list, model detail |
| `account.html` | Personal cabinet | Profile, tier ladder, orders, own models, addresses, payment, notifications, security |
| `auth.html` | Sign in / register | Three modes as a page; the same three exist as a popup on every screen |
| `configurator.html` | Configurator | Customer steps 1–4: model, material/scenario, up to 4 colours, resize, quantity ladder, post-processing, rush — with the itemised price and per-option delta |
| `checkout.html` | Checkout | Step 5: register/sign in, delivery, payment |
| `cabinet.html` | Customer cabinet | Steps 9–10: the 9-stage pipeline, queue position, SLA credit, order history |
| `orders.html` | Order desk | All orders, status advance, refunds, margin |
| `fleet.html` | Printers | Management 2–3: state counters, sortable table, detail popup with service card and AMS slots |
| `materials.html` | Materials | Management 1: status counters, sortable table, detail popup with lots, locations, pricing |
| `users.html` | Users and access | Roles, permission matrix, sessions |
| `blog.html` | Journal — index | Featured report, category filter, card grid, archive with dotted leaders, subscribe |
| `blog-post.html` | Journal — report | Contents sidebar, key figures, article body, right rail, reading progress |
| `settings.html` | System settings | 15 sections covering every constant the farm runs on |
| `postproduction.html` | Post-production | Task board, instructions with time norms, operator badges and scorecards |
| `packaging.html` | Packaging | Cut-off clock, completeness check, tare selection, packaging spend |
| `service.html` | Service | Repair / maintenance / installation / material loading / movement tickets, fleet reliability |
| `purchasing.html` | Purchasing | Materials, spare parts, packaging, printers; supplier scorecards, price history |
| `store.html` | Warehouse | Cell map by zone, batches with drying state, movements, turnover, stocktake |
| `logistics.html` | Logistics | Dispatch to cut-off, tracking, carrier scores, zones, returns, delivery accuracy |

## Two realms

The storefront and the farm are one system with two territories, set by a single
attribute on `<html>`:

```html
<html data-realm="public">   <!-- витрина — what a customer sees -->
<html data-realm="control">  <!-- пульт   — what the farm runs on -->
```

**Colour is not used to tell them apart.** In this system a coloured pixel means
machine state, so spending `live`/`warn`/`bad` on "which realm is this" would
make every screen lie a little. The split runs on channels that were still free:

| Channel | ВИТРИНА · public | ПУЛЬТ · control |
|---|---|---|
| **Texture** | none | A hazard rail hatched down the left edge of every screen |
| **Ground** | plain | graph paper — the machine-room floor |
| **Density** | larger nav, more air | compact, mono |
| **Badge** | `Витрина` chip in the bar | `Пульт` chip, hatched |

**Both realms keep the black chrome and the `C:/PRINTORIAN/...` path strip.**
The instrument look is the brand: a storefront that abandons it reads as a
different product rather than the same one facing outward.

The rail does most of the work — it is fixed to the viewport, so it sits in
peripheral vision no matter where you have scrolled, at any zoom, in both
themes — while every button, table and panel underneath stays byte-identical.

**The app bars no longer cross.** A customer's masthead lists only customer
destinations; a control masthead lists only farm ones. Crossing is deliberate,
through the realm badge or the menu — not something you do by misreading a link.

### The menu

The overlay is the one place both realms are visible at once, so it is the one
place the boundary is drawn explicitly: **ВИТРИНА** above, **ПУЛЬТ** below, and
between them a hatched border reading *ГРАНИЦА ДОСТУПА · ТРЕБУЕТСЯ РОЛЬ*.

- Three filters at the top — `Всё` / `Витрина 7` / `Пульт 13`
- Numbering **restarts per territory**: "витрина 03" and "пульт 03" are
  different places, and one list running 1–20 would imply they are ranked
- Every row carries a flag chip — hatched for control, solid for public — so
  the two kinds stay distinguishable even under `Всё`
- Filtering to one side hides the border and the empty territory header
- Rows without permission render dimmed rather than hidden: you cannot ask for
  a role you cannot see (`data-locked` on the row; wire to `actor.permissions`)

**The realm badge** sits in every app bar, says which side you are standing on,
and opens the menu already filtered to the *other* side — because the only
reason anyone looks at it is to cross.

### Language

`RU` / `EN` sits in the OS bar, immediately right of `PRINTORIAN OS ./v2.0`, on
every screen in both realms. It belongs there rather than in the app bar for the
same reason the version string does: the choice is a property of the whole
console, not of the section you happen to be in.

One switch per page — the modals' chrome bars deliberately do not repeat it.

## The dashboard

Four questions, each given the shape that answers it:

| Question | Visualization | Why not a table |
|---|---|---|
| What is each machine doing? | **Status wall** — one glowing square per printer, grouped by shop zone so position still says *where* | Twelve rows of text cannot be read from across the room; twelve coloured squares can |
| When does a machine free up? | **12-hour schedule** with a live `now` line | "Free at 21:10" is a fact about time, and time needs an axis |
| Will we run out of filament? | **Stacked bar** per material: loaded / in stock / already committed to the queue | The committed column is the number nobody tracks and the one that causes the stall |
| Where are the orders stuck? | **Stage funnel** — count per stage, longest bar is the bottleneck | Comparing nine numbers is a length comparison, not a reading task |

Plus KPI tiles for orders (new today, month-to-date, in progress, average
order), printers (utilisation, efficiency, run hours, idle cost) and finance
(received, spend, profit, and where the spend went), each with a delta against
the previous period. Direction is not sentiment — spend rising is red, revenue
rising is green.

Clicking any square opens the printer popup: current task with progress and
customer, live telemetry, AMS slots with the loaded filament, machine economics,
and that machine's own queue.

**On the glow.** Colour already means machine state everywhere in this system;
the glow is the same signal at a different intensity, so a wall of squares reads
before you focus on any one of them. Offline emits nothing, which is the point.
A printing machine breathes and an errored one blinks — both stop under
`prefers-reduced-motion`.

## The catalogue

Eight sort keys — popularity, price, print time, volume, difficulty, rating,
times printed, recency. Cost-like keys open ascending, quality-like keys open
descending, and clicking the active key flips it (the table-header gesture,
already learned elsewhere in the app).

Facets are **OR within a group, AND across groups**: "PLA or PETG, and small".
Search, facets and sort feed a single pass, so sorting never clears a filter and
searching never ignores the facets — the classic catalogue failure.

Previews are inline SVG line drawings on graph paper rather than photos: an
engineering drawing is honest about a part that does not exist yet, and it
survives both themes. Grid and list are the same cards under a different class,
so switching keeps scroll position.

Every model carries **time and price as measured facts from the last real
print**, not an estimate from volume — that is the whole claim of the page.

## The three shop-floor screens

Post-production, packaging and service are the same problem — an operator's
shift — so they share one vocabulary and answer the same three questions in the
same order:

| Question | Shape |
|---|---|
| What do I do next? | A **task board**, ordered by urgency, with a priority stripe and a countdown against the promise |
| How do I do it? | A **numbered instruction** carrying a *time norm per step*, plus a warning block for the thing that actually causes returns |
| How am I doing? | **Earned badges** and a scorecard — norm vs fact, continuously |

The norm-per-step is the load-bearing idea. A norm you only hear about when you
miss it is a stick; the same norm shown next to the checkbox is a gauge, and it
tells a new operator how long the job should take *before* they start.

Badges are monochrome — three tiers as outline / bright outline / filled. Colour
is never used for tier, because colour is reserved for machine state and a badge
is not a state. Unearned badges are shown dimmed rather than hidden, so there is
something visible to earn. All of them accrue from recorded facts; none can be
awarded by hand.

Each screen keeps its own domain: post-production has operations against
норма-часы and returns from QC; packaging is driven by the courier **cut-off
clock** and completeness; service spans repair, scheduled maintenance, new
printer installation, material loading and moving batches between posts.

## Supply chain

`purchasing.html` → `store.html` → `logistics.html` follow one chain, and each
carries an accumulated evaluation of the counterparty or the stock:

- **Purchasing** scores suppliers on delivery punctuality, defect rate and price
  over their whole history — 3D-Партс is visibly sliding at 6.2 with three late
  deliveries in a row, and the screen says what that already cost.
- **Store** is a cell map by zone, batches under FIFO with drying state (the
  actual cause of most filament breaks), turnover per class, and a dead-stock
  list with the money sitting in it.
- **Logistics** works to the same cut-off as packaging, scores carriers, and
  compares promised against actual delivery time per zone — Урал is at 78%,
  which argues for changing the promise rather than paying penalties.

## Tabs

`account.html` and `settings.html` switch sections in place. Three parts:

- **The rail marker travels.** One bar moves between entries rather than one
  blinking off and another on — the movement is what says the two rows are the
  same control in two states.
- **The panel resolves in.** A left-edge wipe, then its blocks on a short
  stagger, plus one scan pass — the nav overlay's grammar, a notch quieter.
  ~360 ms end to end, once per switch, nothing loops. `prefers-reduced-motion`
  removes all of it.
- **Re-triggerable.** The switcher reads `offsetWidth` between removing and
  adding the class, forcing a style flush; without it the browser coalesces
  remove+add into a no-op and only the first switch would ever animate.

Opt in with `.hv-rail` on the tab list and `.hv-tabview` on the panel container.

### The bug this uncovered

The tabs were switching the `hidden` property correctly and **nothing was
happening on screen**: every panel rendered, stacked, and the page just got
longer. `.hv-stack { display: flex }` and the browser's `[hidden] { display:
none }` have the same specificity (0,1,0), and an author rule beats the UA
sheet — so any hidden element that also carried a layout class stayed visible.

`account.html` was showing all 7 sections at once, `settings.html` all 15,
`auth.html` all 3 sign-in modes, `checkout.html` both forms. Fixed globally in
the reset:

```css
[hidden] { display: none !important; }
```

`hidden` means "not relevant", which outranks any opinion about `display`.
Account went from ~4 screens of scroll to 1.1; settings from 15 stacked
sections to 1.3.

## The cabinet and the door

`account.html` is the customer's own record: identity plate, **tier ladder**
showing the distance to Gold rather than the badge they already have, lifetime
figures, then seven sections — profile, orders, own uploaded models, addresses,
payment and documents, notifications, security with active sessions.

`auth.html` is a two-panel screen: the argument on the left, the form on the
right. Three modes — вход / регистрация / восстановление. Recovery sends a
six-digit code rather than a link, because a code cannot be forwarded by
accident.

**The same three modes also exist as a popup**, injected by `js/auth.js` on every
page. Open it from any element carrying `data-auth-open`, optionally naming the
starting mode:

```html
<button data-auth-open="signup">Зарегистрироваться</button>
```

That matters most at the checkout, where navigating away to a login page loses a
configured quote. The password meter is deliberately crude and says so — length
first, variety second; a meter claiming more precision than it has just teaches
people to game it.

## The settings screen

Fifteen sections, ~100 parameters, switched in place from the left tree:

`Общие` · `Ценообразование` · `Скидки и тарифы` · `Планировщик` · `Сроки и SLA` ·
`Склад и материалы` · `Оборудование и сервис` · `Постобработка` · `Логистика` ·
`Финансы` · `Уведомления` · `Доступ и безопасность` · `Интеграции` ·
`Диагностика` · `Обслуживание системы`

**The parameter names and defaults are real**, read out of the backend rather than
invented — `RateSnapshot` (`margin_percent`, `failure_buffer_percent`,
`multicolor_purge_grams_per_extra_color`, `guard_tier_cliffs`, …),
`SchedulingPolicy` (`weight_material_headroom`, `load_horizon_minutes`,
`comfortable_headroom`, …), the SLA ladder (`percent_per_day`, `max_percent`),
and `core/config.py` (`farm_open_hour`, `price_variance_tolerance`,
`session_ttl_hours`, `telemetry_poll_seconds`, …). Every row shows its identifier
under the label, so this screen, a log line and a support conversation all use the
same word for the same thing.

Editing a row marks it, reveals the value it had, offers a revert, and counts into
the save bar. Diagnostics is read-only by design — it reports what the system
currently thinks of itself, and nothing there is a setting.

## The navigation overlay

Open it with the **Меню** button, `Ctrl`/`Cmd`+`K`, or `/`. Type to filter,
`↑``↓` to move, `↵` to go, `Esc` to close. It opens already sitting on the
current page's own entry, so you always start oriented.

It is a full-screen console rather than a dropdown — the same window chrome as a
real screen, so it reads as switching subsystems, not opening a drawer. On open:
the backdrop scales in, corner brackets draw outward, one scan pass crosses the
field, entries wipe in on a 34 ms stagger, and the highlighted label resolves out
of noise. The whole sequence finishes at ~660 ms and nothing loops afterwards
except the command cursor. `prefers-reduced-motion` collapses all of it — the
stagger, the scan and the decode go, the structure stays.

The route table lives at the top of `js/menu.js`. On integration it becomes one
React component fed by the permission list — which is why it is a single source
here rather than markup copied into ten files.

## The rules the system runs on

1. **Contrast comes from the line, not the fill.** Hairlines on near-black. Corners
   are brackets, drawn as background gradients so both pseudo-elements stay free.
2. **Colour means machine state.** Monochrome until something is happening; then
   `live` / `good` / `warn` / `bad`. No decorative accent exists.
3. **Uppercase technical labels are tracked; prose is not.** Three tracking tokens.
4. **Tabular figures everywhere a number can be compared**, right-aligned, with the
   basis of the calculation set quietly underneath it.
5. **Nothing is rounded.** `--hv-radius: 0`.

## Notes for integration

Tokens are prefixed `--hv-*` so they sit alongside the current `--pr-*` set without
collision during the transition. Classes already map onto what exists:

| Class | Component |
|---|---|
| `.hv-table` | `packages/ui` · DataTable |
| `.hv-tag` | `packages/ui` · StatusTags |
| `.hv-leader` | `packages/ui` · PriceBreakdown |
| `.hv-option__delta` | `packages/ui` · DeltaPreview |
| `.hv-state` | `printer.state.*` |
| `.hv-pipe` | `ordering` · OrderStatus |
| `.hv-appbar` | `apps/web` · App.tsx nav |
| `.hv-menu` | new — nav overlay, driven by `actor.permissions` |
| `.hv-article` | new — journal, no backend counterpart yet |
| `.hv-set` / `.hv-switch` / `.hv-unit` | new — settings rows, bound to `RateSnapshot`, `SchedulingPolicy`, `core.config` |
| `.hv-node` / `.hv-kpi` / `.hv-gantt` | new — status wall, KPI tiles, schedule; fed by the `fleet` event stream |
| `.hv-model` / `.hv-sort` / `.hv-facet` | new — catalogue, `catalog` context |
| `.hv-board` / `.hv-task` / `.hv-step` | new — shop-floor task boards and instructions |
| `.hv-badge` / `.hv-blocks` / `.hv-score` | new — accumulated operator and supplier evaluation |
| `.hv-avatar` / `.hv-tier` / `.hv-record` | new — cabinet, `identity` + `pricing.CustomerTier` |
| `.hv-auth` / `.hv-pw` / `.hv-otp` | new — the door, `identity` |

Fonts are self-hosted in `packages/ui/src/harvester/fonts/` — the farm runs on its
own LAN, where `fonts.googleapis.com` does not resolve.

**None of the three kit faces can render Russian.** Orbitron and Share Tech Mono
are Latin-only; Chakra Petch ships latin, latin-ext, thai and vietnamese and no
range covering U+04xx. In this static kit that is invisible, because the browser
falls back per glyph and the Windows fallbacks look plausible — but the tracked
console character never reaches the words, which are the whole UI.

Each role is therefore two faces, split by `unicode-range`: Orbitron + **Play**,
Chakra Petch + **Exo 2**, Share Tech Mono + **JetBrains Mono**. Latin — codes,
identifiers, figures — comes from the kit's own faces, so the design holds exactly
where it was drawn.

## Two deliberate departures from the sketches

- **The 9px annotation tone was lightened** to clear 4.5:1. At the sketches' value it
  measured 3.1:1, which turns the smallest type in the system into texture.
- **The display face is stretched horizontally** (`--hv-stretch`) to approach the
  Eurostile-Extended proportion, since no free webfont supplies it. The stretch
  switches off below 680px, where a transform would overflow the viewport.
