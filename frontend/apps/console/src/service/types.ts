/**
 * The shape of what `/service/tickets` and `/service/reliability` serve, mirrored
 * by hand — the console screens type their own rows (the purchasing precedent);
 * the generated client covers the transport.
 *
 * **No money anywhere.** The kit's «Последствия» prices a ticket and «Потеря
 * 3 820 ₽» heads the reliability panel; neither figure exists on these routes, by
 * design (`contexts/service/schemas.py`). Both are `VIEW_PRODUCTION` reads, and
 * a response a production role can read must not carry rubles.
 */

/** The kit's five kinds, as the server spells them. A sixth is a compile error here. */
export type TicketKind = 'install' | 'repair' | 'maintenance' | 'material_load' | 'move'

export type TicketStatus = 'open' | 'in_progress' | 'closed'

/** Who raised it — the sweep from a driver-reported failure, or a person. */
export type TicketOrigin = 'driver' | 'person'

export interface TicketStep {
  position: number
  title: string
  note: string | null
  /** Null is "no norm", never zero minutes. */
  norm_minutes: number | null
  done_at: string | null
  done_by: string | null
}

export interface Ticket {
  id: string
  number: string
  kind: TicketKind
  status: TicketStatus
  origin: TicketOrigin
  printer_id: string | null
  failure_id: string | null
  /** Empty for a driver-opened ticket: the screen draws «Сообщил драйвер» from `origin`. */
  title: string
  note: string | null
  norm_minutes: number | null
  opened_at: string
  started_at: string | null
  closed_at: string | null
  opened_by: string | null
  assignee_id: string | null
  /** Since `started_at` (or `opened_at` while merely raised) until `closed_at` or the read. */
  elapsed_seconds: number
  steps: TicketStep[]
  steps_done: number
}

/** The kit's five lanes. The server puts each ticket in exactly one. */
export interface TicketBoard {
  emergency: Ticket[]
  planned: Ticket[]
  in_progress: Ticket[]
  logistics: Ticket[]
  closed: Ticket[]
  closed_since: string | null
}

export interface PrinterLabel {
  id: string
  name: string
  is_active: boolean
}

export interface TicketBoardView {
  board: TicketBoard
  printers: PrinterLabel[]
  at: string
}

/**
 * One row of «Надёжность» from `GET /service/reliability`. Nulls are absences:
 * `observed_seconds` null means no rollup covered the machine, and a rate over
 * that is null too — not zero, which would be a measurement (ADR-0007).
 */
export interface ReliabilityRow {
  printer_id: string
  printer_name: string
  state: string
  failures: number
  closed_failures: number
  open_failures: number
  observed_seconds: string | null
  failures_per_1000_hours: string | null
  mttr_minutes: string | null
}

export interface CauseCount {
  cause: string
  count: number
}

export interface ReliabilityReport {
  rows: ReliabilityRow[]
  causes: CauseCount[]
  /** Failures nobody has named a cause for — every driver-opened one starts here. */
  uncategorised: number
  printers_reporting: number
  printers_listed: number
}

export const ALL_KINDS: readonly TicketKind[] = [
  'install',
  'repair',
  'maintenance',
  'material_load',
  'move',
]
