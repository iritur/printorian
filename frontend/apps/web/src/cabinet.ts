import type { Breakdown, Locale } from '@printorian/ui'

// Re-exported so the cabinet's own modules keep one import. The helper is a
// property of the language and lives in the i18n package.
export { plural } from '@printorian/ui'

/**
 * The order-tracking screen's shapes, and the one derivation it does.
 *
 * That derivation is `pipeline()`. Everything else here is types and
 * formatting; the pipeline is the screen's whole argument — nine stages, each
 * dated from something that actually happened — and it is separated from the
 * rendering so it can be reasoned about, and later tested, without a DOM.
 */

export interface OrderLine {
  id: string
  model_name: string
  /** The stored geometry, when the line was placed from an upload. */
  model_asset_id: string | null
  material_code: string
  quantity: number
  scale: string
  rush: boolean
  colors: string[]
  finishes: string[]
  estimated_minutes: string
  estimated_grams: string
  line_total: string
}

export interface OrderEvent {
  sequence: number
  from_status: string | null
  to_status: string
  reason: string
  created_at: string
  details: Record<string, unknown>
}

export interface Order {
  id: string
  number: string
  status: string
  currency: string
  total: string
  sla_credit: string
  /** Computed server-side: the total less any lateness credit. */
  payable_now: string
  promised_at: string | null
  paid_at: string | null
  shipped_at: string | null
  created_at: string
  delivery_method: string
  delivery_city: string
  delivery_postcode: string
  delivery_address: string
  price_breakdown: Breakdown
  lines: OrderLine[]
  events: OrderEvent[]
}

export interface QueuePosition {
  job_status: string
  position: number | null
  reason: string | null
  predicted_start: string | null
  progress_percent: number | null
  attempt: number
  printer_id: string | null
  assigned_at: string | null
  started_at: string | null
}

export interface Machine {
  name: string
  brand: string
  model: string
  state: string
  progress_percent: number | null
  remaining_minutes: number | null
  eta: string | null
  layer_current: number | null
  layer_total: number | null
}

export interface Progress {
  queue: QueuePosition | null
  machine: Machine | null
}

/** The kit's «—». Absent is not zero, everywhere on this screen. */
export const NONE = '—'

/** Statuses that mean the order stopped rather than finished. */
export const HALTED = ['cancelled', 'refunded']

/** Statuses whose orders are done with — shipped, completed, or halted. */
export const CLOSED = ['shipped', 'completed', ...HALTED]

/**
 * A status, as one of the kit's four `data-state` colours.
 *
 * Four, not thirteen: there are four things a customer needs to tell apart —
 * running, waiting, finished, gone wrong — and a palette with one entry per
 * state machine node is a legend, not a signal. The *caption* still comes from
 * the shared catalogue, so an order never reads as two different things here
 * and on the account screen.
 */
export function stateOf(status: string): string {
  if (['printing', 'paid', 'prep', 'post_production', 'quality_check', 'packing'].includes(status))
    return 'printing'
  if (['shipped', 'completed'].includes(status)) return 'finished'
  if (HALTED.includes(status)) return 'offline'
  return 'paused'
}

/**
 * The nine stages, and the order status each is reached by.
 *
 * `Назначен` has no order status — assignment is a *job* event, and the order
 * stays `queued` through it. It is dated from the queue instead, which is also
 * where the machine's name comes from.
 */
export const STAGES: { key: string; label: string; status: string | null }[] = [
  { key: 'paid', label: 'Оплачен', status: 'paid' },
  { key: 'prep', label: 'Подготовка', status: 'prep' },
  { key: 'queued', label: 'В очереди', status: 'queued' },
  { key: 'assigned', label: 'Назначен', status: null },
  { key: 'printing', label: 'Печатается', status: 'printing' },
  { key: 'post_production', label: 'Постобработка', status: 'post_production' },
  { key: 'quality_check', label: 'Контроль', status: 'quality_check' },
  { key: 'packing', label: 'Упаковка', status: 'packing' },
  { key: 'shipped', label: 'Отправлен', status: 'shipped' },
]

export interface Stage {
  key: string
  label: string
  /**
   * `done` and `now` are the kit's; `skipped` and `pending` both render unlit
   * and read very differently. An order that shipped without ever passing
   * quality control did not *leave that stage pending* — nothing recorded it,
   * and saying «—» there implies it is still to come.
   */
  state: 'done' | 'now' | 'skipped' | 'pending'
  /** ISO, or `null` when the stage has no time to show. */
  at: string | null
  /** Replaces the time when there is something better to say. */
  note?: string
}

/**
 * The nine stages of one order, dated from its own event log.
 *
 * Read from events rather than from `status` alone, because a status says only
 * where the order is *now* — it cannot date the four stages behind it, and the
 * whole point of the pipeline is that each stage carries the moment it happened.
 *
 * Three things fall out of doing it this way and none of them are special cases:
 *
 * * a stage the order passed *through* has an event and is `done`;
 * * a stage it passed *over* has none and is `skipped` — which is the honest
 *   rendering of `post_production` and `quality_check` today, since no context
 *   advances them and orders go from printing to packing;
 * * a stage it has not reached is `pending`.
 */
export function pipeline(order: Order, progress: Progress | null): Stage[] {
  const seen = new Map<string, string>()
  for (const event of order.events) {
    if (!seen.has(event.to_status)) seen.set(event.to_status, event.created_at)
  }

  const assigned = progress?.queue?.assigned_at ?? null
  const reached = (stage: (typeof STAGES)[number]): string | null =>
    stage.status === null ? assigned : (seen.get(stage.status) ?? null)

  // How far the order has actually got, as an index into `STAGES`. The *last*
  // stage with a timestamp, not the first gap: an order that skipped a stage
  // still moved past it, and treating the gap as the frontier would freeze the
  // pipeline at 06 for every order the farm has ever shipped.
  let frontier = -1
  STAGES.forEach((stage, index) => {
    if (reached(stage) !== null) frontier = index
  })

  return STAGES.map((stage, index) => {
    const at = reached(stage)
    if (at !== null) {
      return {
        key: stage.key,
        label: stage.label,
        state: index === frontier ? 'now' : 'done',
        at,
      }
    }
    return {
      key: stage.key,
      label: stage.label,
      state: index < frontier ? 'skipped' : 'pending',
      at: null,
    }
  })
}

/** `08.08 14:07` — the kit's stage and history stamp. */
export function stamp(iso: string | null, locale: Locale): string {
  if (!iso) return NONE
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return NONE
  const tag = locale === 'ru' ? 'ru-RU' : 'en-GB'
  return `${at.toLocaleDateString(tag, { day: '2-digit', month: '2-digit' })} ${at.toLocaleTimeString(
    tag,
    { hour: '2-digit', minute: '2-digit' },
  )}`
}

/** `10.08 · 09:00` — the kit's near-term stamp, for dates days rather than months away. */
export function shortWhen(iso: string | null, locale: Locale): string {
  if (!iso) return NONE
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return NONE
  const tag = locale === 'ru' ? 'ru-RU' : 'en-GB'
  return `${at.toLocaleDateString(tag, { day: '2-digit', month: '2-digit' })} · ${at.toLocaleTimeString(
    tag,
    { hour: '2-digit', minute: '2-digit' },
  )}`
}

/** `11.08.2026 18:00` — the promised date, which is worth the year. */
export function fullStamp(iso: string | null, locale: Locale): string {
  if (!iso) return NONE
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return NONE
  const tag = locale === 'ru' ? 'ru-RU' : 'en-GB'
  return `${at.toLocaleDateString(tag)} ${at.toLocaleTimeString(tag, {
    hour: '2-digit',
    minute: '2-digit',
  })}`
}

/** `7 ч 26 м`, from a count of minutes. */
export function duration(minutes: number | null, locale: Locale): string {
  if (minutes === null || !Number.isFinite(minutes) || minutes < 0) return NONE
  const hours = Math.floor(minutes / 60)
  const rest = Math.round(minutes % 60)
  const h = locale === 'ru' ? 'ч' : 'h'
  const m = locale === 'ru' ? 'м' : 'm'
  return hours > 0 ? `${hours} ${h} ${rest} ${m}` : `${rest} ${m}`
}

/**
 * Hours past the promise, or `null` while there is still time.
 *
 * Computed here rather than read from the server because it changes every
 * minute and the server would be reporting a figure stale on arrival. The
 * *money* is not computed here — `sla_credit` is what the farm has actually
 * accrued, and a client that worked out its own would eventually disagree with
 * the refund.
 */
export function overdueHours(order: Order, now: Date = new Date()): number | null {
  if (!order.promised_at) return null
  // A cancelled order cannot be late. It had a promised date and the farm is no
  // longer working towards it, so counting the hours since would have the screen
  // accruing lateness against work nobody is doing — and the figure would grow
  // forever beside a credit that is correctly nought.
  if (HALTED.includes(order.status)) return null
  // The clock stops when the parcel leaves. An order shipped late stays late by
  // the amount it was late by, rather than growing forever afterwards.
  const end = order.shipped_at ? new Date(order.shipped_at) : now
  const late = end.getTime() - new Date(order.promised_at).getTime()
  return late > 0 ? late / 3_600_000 : null
}
