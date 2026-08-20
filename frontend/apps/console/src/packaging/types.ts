/**
 * The shape of `GET /packaging/board`.
 *
 * Minutes, masses, millimetres and money arrive as decimal **strings**, like
 * every other figure on the wire: exactness survives that way, and a packing
 * cost of 218.40 ₽ should not become 218.40000000000003 on the way to a screen
 * somebody is judged by.
 */

export type PackStatus = 'checked' | 'packing' | 'held' | 'ready' | 'shipped' | 'cancelled'

export type HoldReason =
  | 'invoice_unpaid'
  | 'waybill_missing'
  | 'address_incomplete'
  | 'item_missing'

export type TaraKind = 'bag' | 'box' | 'wrap' | 'filler'

export type Urgency = 'late' | 'soon' | 'ok'

export interface PackStep {
  position: number
  title: string
  detail: string | null
  warning: string | null
  norm_minutes: string
  /** `null` until the step is ticked. The fact half of the pair. */
  actual_minutes: string | null
  done_at: string | null
}

export interface PackLine {
  model_name: string
  color: string
  ordered: number
  present: number
}

export interface Parcel {
  id: string
  number: string
  status: PackStatus
  order_id: string
  order_number: string
  delivery_method: string
  carrier_code: string

  cutoff_at: string | null
  urgency: Urgency
  /** Signed: negative means the van has already gone. */
  minutes_to_cutoff: string | null

  items: number
  estimated_grams: string
  length_mm: string
  width_mm: string
  height_mm: string
  /** What the carrier bills when the parcel is bigger than it is heavy. */
  volumetric_grams: string
  wrap_required: boolean

  tara_id: string | null
  tara_name: string
  /** What the geometry says, whether or not the packer agreed. */
  recommended_tara_id: string | null
  recommended_tara_name: string
  weight_grams: string | null
  packaging_cost: string

  norm_minutes: string
  elapsed_minutes: string
  instruction_version: string
  /** Above 100 is faster than the norm. `null` when nothing is recorded. */
  pace_percent: string | null
  projected_minutes: string | null

  operator_id: string | null
  operator_name: string
  started_at: string | null
  finished_at: string | null
  shipped_at: string | null

  hold_reason: HoldReason | null
  discrepancy_code: string | null
  discrepancy_note: string | null
  discrepancy_at: string | null

  steps: PackStep[]
  lines: PackLine[]
}

export interface PackColumn {
  status: PackStatus
  tasks: Parcel[]
}

export interface TaraRow {
  id: string
  code: string
  name: string
  kind: TaraKind
  unit: string
  inner_length_mm: string | null
  inner_width_mm: string | null
  inner_height_mm: string | null
  price: string
  stock: string
  reorder_at: string
  used_per_month: string
  /** `null` when nothing has been consumed — no rate, not "for ever". */
  months_left: string | null
}

export interface PackKpi {
  queued: number
  queued_by_method: [string, number][]
  urgent: number
  due_before_cutoff: number
  packed_today: number
  packed_yesterday: number
  average_minutes: string | null
  norm_minutes: string | null
  pace_percent: string | null
  /** `null` when nothing has shipped — no record is not a clean record. */
  days_without_discrepancy: number | null
  discrepancies: number
  cost_per_parcel: string | null
}

export interface PackMetrics {
  days: number
  packed: number
  average_minutes: string | null
  tara_accuracy_percent: string | null
  discrepancies: number
  /** `null` until logistics can mark a shipment damaged. Rendered as a dash. */
  damages: number | null
  missed_cutoffs: number
  cost_per_parcel: string | null
  score: string | null
}

export interface Badge {
  code: string
  /** 0 means not yet earned; the badge is still shown, dimmed. */
  tier: number
  detail: Record<string, string>
}

export interface PackScore {
  operator_id: string
  operator_name: string
  packed: number
  average_minutes: string | null
  discrepancies: number
  pace_percent: string | null
  score: string | null
  badges: Badge[]
}

export interface Pickup {
  method: string
  carrier_code: string
  at: string | null
  parcels: number
}

export interface PackBoard {
  at: string
  next_cutoff_at: string | null
  columns: PackColumn[]
  kpi: PackKpi
  tara: TaraRow[]
  metrics: PackMetrics
  shift: PackScore[]
  pickups: Pickup[]
}

/** What a packer can report at the completeness check. Codes, rendered here. */
export const DISCREPANCY_CODES = [
  'discrepancy.short_count',
  'discrepancy.wrong_colour',
  'discrepancy.damaged_part',
  'discrepancy.missing_document',
] as const

/** Why a parcel gets parked on somebody else's problem. */
export const HOLD_REASONS: HoldReason[] = [
  'invoice_unpaid',
  'waybill_missing',
  'address_incomplete',
]
