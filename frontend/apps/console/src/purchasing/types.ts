/**
 * The shape of what `/purchasing` serves, mirrored by hand.
 *
 * The console screens type their own rows (the packaging precedent): the
 * generated client covers the transport and these describe what a screen reads.
 * Quantities and money arrive as decimal **strings**, like every other figure on
 * the wire — 1 800.00 ₽ must not become 1800.0000000000002 on the way to a
 * person who signs for it.
 *
 * **Two families, and the split is the whole design.** `PurchaseOrderView` and
 * `PurchasingBoard` carry no ruble at all; prices live in `PurchaseOrderCost`,
 * which arrives from its own route behind `VIEW_FINANCIALS`. A manager without
 * that permission is refused the route rather than served the same body with the
 * money nulled — a null here already means "not measured" (ADR-0007), and one
 * spelling for two facts is how a screen starts lying quietly.
 */

/**
 * The six stages plus cancellation, as the server spells them.
 *
 * Kept as a union rather than a `string` so a stage the server adds tomorrow is
 * a compile error here and an «unknown» label at runtime — never the last stage
 * this build happens to know. `PurchasingPage.test.tsx` pins that.
 */
export type PurchaseStatus =
  | 'draft'
  | 'approved'
  | 'paid'
  | 'in_transit'
  | 'receiving'
  | 'stored'
  | 'cancelled'

/** The five classes the farm buys. Only `material` can be received in this build. */
export type PurchasableKind =
  | 'material'
  | 'printer'
  | 'spare_part'
  | 'packaging'
  | 'post_consumable'

/**
 * Why a low item matters, in whichever terms the farm can actually measure.
 *
 * There is deliberately no "coverage in months" arm. `design/purchasing.html`
 * shows «1.2 месяца», and nothing in this system measures material consumption:
 * `material_lots.remaining_grams` is written once, at lot creation, and never
 * decremented. So the row says `not_measured` and this screen draws an em dash.
 */
export type ConsequenceKind = 'committed_work' | 'not_measured'

export interface ReorderConsequence {
  kind: ConsequenceKind
  /** Grams of queued work waiting on this material. Null is "not measured". */
  committed_grams: string | null
  committed_jobs: number | null
}

export interface ReorderRow {
  kind: PurchasableKind
  item_code: string
  item_name: string
  /** Measured, and never null on a row that exists: an unknown level is not listed. */
  remaining: string
  unit: string
  threshold: string
  consequence: ReorderConsequence
}

export interface PurchaseStatusCount {
  status: PurchaseStatus
  count: number
}

export interface PurchaseOrderRow {
  id: string
  number: string
  status: PurchaseStatus
  /** Null while the draft's supplier is still «не выбран». */
  supplier_code: string | null
  supplier_name: string | null
  line_count: number
  total_quantity: string
  expected_at: string | null
  created_at: string
}

export interface PurchasingBoard {
  at: string
  reorder: ReorderRow[]
  orders: PurchaseOrderRow[]
  counts: PurchaseStatusCount[]
  total: number
}

export interface PurchaseStageView {
  status: PurchaseStatus
  /** Null for a stage not reached — and for one skipped, which is the case that matters. */
  at: string | null
  is_current: boolean
}

export interface PurchaseReceiptView {
  id: string
  line_id: string
  quantity: string
  lot_number: string | null
  material_lot_id: string | null
  received_at: string
  received_by: string | null
}

export interface PurchaseLineView {
  id: string
  kind: PurchasableKind
  item_code: string
  item_name: string
  quantity: string
  unit: string
  /** Summed from the receipts by the server, never stored. */
  received_quantity: string
  /** Whether this build can put an arrival of this class into stock at all. */
  is_receivable: boolean
}

export interface SupplierView {
  id: string
  code: string
  name: string
  kinds: string[]
  is_active: boolean
}

export interface PurchaseOrderView {
  id: string
  number: string
  status: PurchaseStatus
  supplier: SupplierView | null
  note: string | null
  expected_at: string | null
  created_at: string
  stages: PurchaseStageView[]
  lines: PurchaseLineView[]
  receipts: PurchaseReceiptView[]
}

export interface PurchaseLineCost {
  line_id: string
  item_code: string
  quantity: string
  /** Null when nobody has quoted it. A draft off a threshold has no price. */
  unit_price: string | null
  total: string | null
}

export interface PurchaseOrderCost {
  order_id: string
  number: string
  lines: PurchaseLineCost[]
  /** Null while any line is unpriced — a partial sum labelled "total" is a lie. */
  total: string | null
  unpriced_lines: number
  frozen_in_stock: string | null
}

/**
 * The stages the pipe draws, in order, and the only ones it will draw.
 *
 * A whitelist rather than "whatever the server sent", for the reason
 * `DiagnosticsPanel` keeps one: a stage this build has never heard of must
 * render unnamed, not borrow the label of the last stage in the list. Adding one
 * on the server without adding it here is then visible instead of wrong.
 */
export const PIPE: readonly PurchaseStatus[] = [
  'draft',
  'approved',
  'paid',
  'in_transit',
  'receiving',
  'stored',
]

/** Every stage the chips can filter on, cancellation included. */
export const ALL_STATUSES: readonly PurchaseStatus[] = [...PIPE, 'cancelled']
