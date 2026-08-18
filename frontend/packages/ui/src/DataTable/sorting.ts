import type { Column, SortDirection, SortState, Sortable } from './types'

export function isMissing(value: Sortable): boolean {
  return value === null || value === undefined
}

/**
 * Compare two present values with a total order.
 *
 * Missing values are *not* handled here, because the direction factor applied by
 * `sortRows` would negate that handling and float them to the top of a descending
 * sort. They are dealt with in `sortRows`, outside the factor.
 */
export function compareValues(a: Sortable, b: Sortable): number {
  if (isMissing(a) && isMissing(b)) return 0
  if (isMissing(a)) return 1
  if (isMissing(b)) return -1

  if (a instanceof Date && b instanceof Date) return a.getTime() - b.getTime()
  if (typeof a === 'number' && typeof b === 'number') return a - b
  if (typeof a === 'boolean' && typeof b === 'boolean') {
    return Number(a) - Number(b)
  }

  // Locale-aware so Cyrillic sorts correctly (ADR-0012: RU + EN from day one).
  return String(a).localeCompare(String(b), undefined, {
    numeric: true,
    sensitivity: 'base',
  })
}

export function sortRows<T>(
  rows: readonly T[],
  columns: ReadonlyArray<Column<T>>,
  sort: SortState | null,
): readonly T[] {
  if (!sort) return rows

  const column = columns.find((candidate) => candidate.key === sort.key)
  if (!column) return rows

  const factor = sort.direction === 'asc' ? 1 : -1

  // Copy first: mutating the caller's array would fight React's change detection.
  return [...rows].sort((a, b) => {
    const left = column.value(a)
    const right = column.value(b)

    // Missing values sort last in BOTH directions, outside the direction factor.
    // "No service date recorded" is not "due first" — floating it to the top of a
    // descending fleet table would actively mislead whoever reads it.
    if (isMissing(left) && isMissing(right)) return 0
    if (isMissing(left)) return 1
    if (isMissing(right)) return -1

    return factor * compareValues(left, right)
  })
}

/**
 * Cycle a header through ascending, descending, then unsorted.
 *
 * The third state matters: it restores the server's ordering, which for a queue
 * is usually the meaningful one.
 */
export function nextSort(current: SortState | null, key: string): SortState | null {
  if (!current || current.key !== key) return { key, direction: 'asc' }
  if (current.direction === 'asc') return { key, direction: 'desc' }
  return null
}

export function ariaSortFor(
  current: SortState | null,
  key: string,
): 'ascending' | 'descending' | 'none' {
  if (!current || current.key !== key) return 'none'
  return current.direction === 'asc' ? 'ascending' : 'descending'
}

export function sortIndicator(direction: SortDirection | null): string {
  if (direction === 'asc') return '▲'
  if (direction === 'desc') return '▼'
  return ''
}
