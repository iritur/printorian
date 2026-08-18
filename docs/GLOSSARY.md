# Domain glossary (RU / EN)

One agreed name per concept. Names drift when a concept is nearly two concepts —
`Spool` / `Material` / `Filament`, or `WorkOrder` / `Order` — and a codebase that
tolerates the drift is one where two parallel models can grow without anyone deciding
to build them.

**Rules**
* The English term is the identifier in code. Always.
* The Russian term is for UI catalogues and conversation, never for identifiers.
* Adding a concept means adding a row here in the same change.

---

## Customer side

| English (code) | Русский | Meaning |
|---|---|---|
| `Customer` | Заказчик | A registered person or company placing orders |
| `Order` | Заказ | What a customer bought. The customer-facing aggregate |
| `OrderLine` | Позиция заказа | One configured model within an order |
| `Configuration` | Конфигурация | The chosen options for a line: material, colours, scale, finishes, quantity |
| `PriceSpec` | Параметры расчёта | Everything the pricing engine needs as input |
| `Breakdown` | Структура цены | The itemized price the customer sees |
| `LineItem` | Статья цены | One row of the breakdown, with its basis |
| `RateSnapshot` | Снимок тарифов | Immutable bundle of every rate used for one calculation |
| `SlaCommitment` | Обязательство по сроку | The promised delivery moment and its decay policy |
| `PriceCredit` | Скидка за просрочку | Credit accrued when the promise is missed |

## Catalogue and preparation

| English (code) | Русский | Meaning |
|---|---|---|
| `ModelAsset` | Модель | An uploaded or catalogued 3D file plus its mesh analysis |
| `Sku` | Артикул | A repeatable catalogue product |
| `PreparedPlate` | Подготовленная плита | Sliced output: exact time and filament, cached and reusable |
| `PrepQueue` | Очередь подготовки | Where paid orders wait for an engineer to slice them |
| `EstimateSource` | Источник оценки | `MeshHeuristic` / `PreparedPlate` / `Measured` |
| `PriceReview` | Пересмотр цены | State entered when actual cost exceeds the quote beyond tolerance |

## Materials

| English (code) | Русский | Meaning |
|---|---|---|
| `MaterialSpec` | Тип материала | Catalogue level: PLA Matte Black, its properties and prices |
| `MaterialLot` | Партия материала | Physical level: this spool, its remaining mass, its location |
| `Location` | Расположение | `stock:shelf-B3` or `printer:P1S-02:ams-A:slot-3` |
| `MaterialStatus` | Статус материала | Derived rollup: `stock` / `in printer` / `ordered` / `none` |
| `Supplier` | Поставщик | Who the material was bought from |
| `PurchaseOrder` | Заказ поставщику | Procurement document |

## Fleet

| English (code) | Русский | Meaning |
|---|---|---|
| `Printer` | Принтер | A machine in the farm |
| `Capabilities` | Возможности | Build volume, nozzle, AMS presence — the scheduler's hard constraints |
| `Telemetry` | Телеметрия | One observation of a machine. Never synthesized |
| `PrinterState` | Состояние принтера | `offline` / `idle` / `printing` / `paused` / `finished` / `error` / `maintenance` |
| `ConnectionMode` | Режим подключения | `lan` / `cloud` / `manual` / `mock` |
| `AmsSlot` | Слот AMS | One material position in an AMS unit |
| `ServiceCard` | Карта обслуживания | Maintenance operations, periodicity, materials used |
| `Driver` | Драйвер | A brand's protocol adapter |

## Production

| English (code) | Русский | Meaning |
|---|---|---|
| `Job` | Задание | One plate on one printer. The atomic unit of machine work |
| `Assignment` | Назначение | The scheduler's decision to put a job on a printer |
| `AssignmentDecision` | Обоснование назначения | The recorded "why": candidates, rejections, scores |
| `WaitListEntry` | Очередь ожидания | A job with no eligible printer, plus its predicted start |
| `PostProductionTask` | Постобработка | A finishing step: assembly, priming, painting, finishing |
| `QcRecord` | Контроль качества | Pass/fail with evidence |
| `Package` / `Shipment` | Упаковка / Отправление | Fulfilment |

## Cross-cutting

| English (code) | Русский | Meaning |
|---|---|---|
| `Actor` | Действующее лицо | The authenticated caller and their resolved permissions |
| `Role` | Роль | `customer` / `operator` / `engineer` / `manager` / `owner` |
| `Permission` | Право | A single capability, granted by the role matrix |
| `Event` | Событие | A published notification, e.g. `job.finished` |
| `Money` | Сумма | Decimal amount plus currency. Never a float |

---

## Names deliberately not used

Each of these reads as one idea and is two, or names a thing this system does not have.

| Not used | Why |
|---|---|
| `WorkOrder` | Conflates the customer's purchase with the farm's work. They are `Order` and `Job` |
| `Spool` | Conflates catalogue identity with physical stock. They are `MaterialSpec` and `MaterialLot` |
| `Web*` (`WebOrder`, `WebMaterial`, `WebUser`) | There is one domain model, and both apps are clients of it (ADR-0001) |
| `PrintJob` | Shortened to `Job`; there is no other kind |
| `Quote` as an entity | Pricing is a pure function; a quote is a stored `Breakdown` + `RateSnapshot` on an order |
