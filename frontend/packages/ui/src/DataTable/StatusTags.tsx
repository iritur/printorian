import { FilterChips } from '../filters/FilterChips'
import type { StatusTag } from './types'

export interface StatusTagsProps<T> {
  rows: readonly T[]
  tags: ReadonlyArray<StatusTag<T>>
  active: string | null
  onToggle: (key: string | null) => void
  allLabel: string
}

/**
 * The counter chips above a table, counted from the table's own rows.
 *
 * The markup and the clearing rule live in `FilterChips`; what stays here is the
 * one thing a table can do that the account history and the journal cannot —
 * count by running each tag's predicate over the rows it already holds. Keeping
 * that adapter separate is what lets the shared component take counts rather
 * than compute them, so a screen whose totals come off the server does not have
 * to invent rows to be counted.
 *
 * Counts are always computed from the unfiltered rows, so switching filters does
 * not change the numbers — the chips are a picture of the whole set, which is
 * what makes "4 in printer, 12 in stock, 2 ordered" readable at a glance.
 */
export function StatusTags<T>({ rows, tags, active, onToggle, allLabel }: StatusTagsProps<T>) {
  return (
    <FilterChips
      label={allLabel}
      all={{ label: allLabel, count: rows.length }}
      chips={tags.map((tag) => ({
        key: tag.key,
        label: tag.label,
        count: rows.filter((row) => tag.match(row)).length,
        tone: tag.tone,
      }))}
      active={active}
      onSelect={onToggle}
    />
  )
}
