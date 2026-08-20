/**
 * The shape of `GET /dashboard`.
 *
 * Hand-written rather than generated, like every other console screen's row
 * types: the generated client covers the *transport*, and these mirror one
 * response. Money and mass arrive as decimal **strings** — exactness survives
 * the wire that way (`core.money`) — so nothing here is typed `number` unless
 * the backend really sent a JSON number.
 */

export type Period = 'today' | 'week' | 'month' | 'quarter'

export const PERIODS: Period[] = ['today', 'week', 'month', 'quarter']

export interface Trend {
  value: string
  previous: string
  /** `null` when there is nothing to compare against, never `0`. */
  change_percent: string | null
}

export interface Window {
  period: Period
  start: string
  end: string
  previous_start: string
}

export interface StatusSlice {
  status: string
  count: number
}

export interface OrdersOverview {
  placed: Trend
  /** This calendar month against the whole of the previous one. */
  placed_month: Trend
  paid: number
  awaiting_payment: number
  in_progress: number
  funnel: StatusSlice[]
  average_order: Trend
  median_order: string
  lines_per_order: string
}

export interface CategorySpend {
  category: string
  amount: string
}

export interface DayRevenue {
  day: string
  amount: string
}

export interface FinanceOverview {
  received: Trend
  spend: Trend
  profit: Trend
  margin_percent: string
  received_today: string
  spend_today: string
  receivable: string
  refund_count: number
  refund_total: string
  spend_by_category: CategorySpend[]
  revenue_by_day: DayRevenue[]
}

export interface WallNode {
  id: string
  name: string
  state: string
  progress_percent: number | null
  eta: string | null
  current_job: string | null
  needs_attention: boolean
  maintenance_due: boolean
  last_seen_at: string | null
}

export interface Zone {
  name: string
  nodes: WallNode[]
  load_percent: string
}

export interface Throughput {
  run_hours: string
  capacity_hours: string
  idle_hours: string
  succeeded: number
  failed: number
  /** `null` when nothing finished — a farm that printed nothing is not perfect. */
  success_percent: string | null
  truncated: boolean
}

export interface HeatRow {
  /** Monday is 0, matching `datetime.weekday()`. The client names it. */
  weekday: number
  /** Twenty-four values, each 0..1 of the farm's capacity for that hour. */
  hours: string[]
}

export interface FleetOverview {
  zones: Zone[]
  counts: { state: string; count: number }[]
  total: number
  printing: number
  attention: number
  utilisation_percent: string
  throughput: Throughput
  hourly_load: HeatRow[]
}

export interface Alert {
  code: string
  tone: string
  subject: string
  subject_id: string | null
  at: string | null
  detail: Record<string, string>
}

export interface ScheduleBar {
  job_id: string
  order_id: string
  /** What the customer's order is called. Resolved server-side, never an id. */
  order_number: string
  status: string
  /** The prepared plate's filename, when the job has one. */
  label: string
  starts_at: string
  ends_at: string
  progress_percent: number | null
}

export interface ScheduleRow {
  printer_id: string
  bars: ScheduleBar[]
  free_at: string | null
}

export interface Schedule {
  starts_at: string
  ends_at: string
  rows: ScheduleRow[]
}

export interface FilamentBar {
  code: string
  name: string
  color_hex: string
  loaded_grams: string
  stock_grams: string
  committed_grams: string
  free_grams: string
  committed_jobs: number
  loaded_printer_ids: string[]
}

export interface FarmSummary {
  at: string
  window: Window
  orders: OrdersOverview
  finance: FinanceOverview
  fleet: FleetOverview
  schedule: Schedule
  filament: FilamentBar[]
  alerts: Alert[]
  wait_list: number
}
