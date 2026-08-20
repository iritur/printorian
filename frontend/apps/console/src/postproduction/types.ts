/**
 * The shape of `GET /postproduction/board`.
 *
 * Minutes and percentages arrive as decimal **strings**, like every other figure
 * on the wire — exactness survives that way (`core.money`), and a pace of 104.3%
 * should not become 104.30000000000001 on the way to a screen an operator is
 * judged by.
 */

export type TaskStatus =
  | 'waiting'
  | 'in_progress'
  | 'paused'
  | 'curing'
  | 'for_qc'
  | 'returned'
  | 'done'
  | 'cancelled'

export type OperationKind =
  | 'support_removal'
  | 'sanding'
  | 'priming'
  | 'painting'
  | 'polishing'
  | 'assembly'

export type Urgency = 'late' | 'soon' | 'ok'

export interface Step {
  position: number
  title: string
  detail: string | null
  warning: string | null
  norm_minutes: string
  /** `null` until the step is ticked. The fact half of the pair. */
  actual_minutes: string | null
  done_at: string | null
}

export interface Task {
  id: string
  number: string
  status: TaskStatus
  kind: OperationKind
  order_id: string
  order_number: string
  model_name: string
  material_code: string
  colors: string[]
  printer_id: string | null
  quantity: number

  due_at: string | null
  urgency: Urgency
  /** Signed: negative means the promise has already passed. */
  minutes_to_due: string | null

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
  cure_until: string | null

  attempt: number
  defect_code: string | null
  defect_note: string | null

  steps: Step[]
}

export interface Column {
  status: TaskStatus
  tasks: Task[]
}

export interface OperationStat {
  kind: OperationKind
  completed: number
  norm_minutes: string
  actual_minutes: string
  pace_percent: string | null
  returns: number
}

export interface ShiftKpi {
  queued: number
  queued_by_kind: [OperationKind, number][]
  urgent: number
  completed_today: number
  completed_yesterday: number
  /** `null` when nothing finished — a shift that did nothing has no quality. */
  quality_percent: string | null
  returns: number
  pace_percent: string | null
  shop_pace_percent: string | null
}

export interface Badge {
  code: string
  /** 0 means not yet earned; the badge is still shown, dimmed. */
  tier: number
  detail: Record<string, string>
}

export interface Scorecard {
  operator_id: string
  operator_name: string
  completed: number
  returns: number
  pace_percent: string | null
  score: string | null
  is_trainee: boolean
  badges: Badge[]
}

export interface Consumable {
  id: string
  code: string
  name: string
  /** A unit code — `sheet`, `can`, `pair`, `litre` — rendered by the client. */
  unit: string
  remaining: string
  reorder_at: string
}

export interface Board {
  at: string
  columns: Column[]
  kpi: ShiftKpi
  operations: OperationStat[]
  shift: Scorecard[]
  consumables: Consumable[]
  output_by_day: [string, number][]
}

/** Defect codes the inspector can pick. Rendered from the catalogue, not typed. */
export const DEFECT_CODES = [
  'defect.thin_wall_broken',
  'defect.paint_run',
  'defect.layer_visible',
  'defect.wrong_colour',
  'defect.incomplete',
  'defect.damaged',
] as const
