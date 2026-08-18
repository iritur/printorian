import type { ReactNode } from 'react'

/** A value the table knows how to order. */
export type Sortable = string | number | boolean | Date | null | undefined

export interface Column<T> {
  /** Stable identifier, also the sort key. */
  key: string
  /** Header text. Callers pass an already-translated string (ADR-0012). */
  header: string
  /** Extracted value, used for sorting and as the default cell content. */
  value: (row: T) => Sortable
  /** Custom cell rendering. Falls back to `value`. */
  render?: (row: T) => ReactNode
  /** Defaults to true — the scenario asks for sorting on table headers. */
  sortable?: boolean
  align?: 'start' | 'end'
  /** Optional fixed width, e.g. '12rem'. */
  width?: string
}

export type Tone = 'neutral' | 'good' | 'warn' | 'bad'

/**
 * A status filter shown above the table as a chip with a live count.
 *
 * This is the scenario's "above table I should see status tags with the number
 * of materials in it", generalized so printers, orders and materials all get it
 * from one component instead of three bespoke screens.
 */
export interface StatusTag<T> {
  key: string
  label: string
  match: (row: T) => boolean
  tone?: Tone
}

export type SortDirection = 'asc' | 'desc'

export interface SortState {
  key: string
  direction: SortDirection
}

export interface DataTableProps<T> {
  rows: readonly T[]
  columns: ReadonlyArray<Column<T>>
  rowKey: (row: T) => string
  statusTags?: ReadonlyArray<StatusTag<T>>
  /** Opens the detail view. The scenario's "popup window with detailed information". */
  onRowActivate?: (row: T) => void
  initialSort?: SortState
  emptyLabel?: string
  /** Accessible caption; also used as the table's aria-label. */
  caption: string
  isLoading?: boolean
  loadingLabel?: string
}
