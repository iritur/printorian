import { useMemo, useState } from 'react'

import { StatusTags } from './StatusTags'
import { ariaSortFor, nextSort, sortIndicator, sortRows } from './sorting'
import type { DataTableProps, SortState } from './types'

/**
 * The one table in Printorian.
 *
 * The scenario asks for the same pattern at least four times — materials,
 * printers, orders, service operations — each with sortable headers, status-tag
 * counters above, and a detail popup. V1 built that four times as bespoke
 * 300–550 line screens that then behaved differently from each other. This is
 * the single implementation they are all configured from.
 */
export function DataTable<T>({
  rows,
  columns,
  rowKey,
  statusTags,
  onRowActivate,
  initialSort,
  emptyLabel = 'No records',
  caption,
  isLoading = false,
  loadingLabel = 'Loading…',
}: DataTableProps<T>) {
  const [sort, setSort] = useState<SortState | null>(initialSort ?? null)
  const [activeTag, setActiveTag] = useState<string | null>(null)

  const filtered = useMemo(() => {
    if (!activeTag || !statusTags) return rows
    const tag = statusTags.find((candidate) => candidate.key === activeTag)
    return tag ? rows.filter((row) => tag.match(row)) : rows
  }, [rows, statusTags, activeTag])

  const visible = useMemo(() => sortRows(filtered, columns, sort), [filtered, columns, sort])

  return (
    <div className="hv-datatable">
      {statusTags && statusTags.length > 0 && (
        <StatusTags
          rows={rows}
          tags={statusTags}
          active={activeTag}
          onToggle={setActiveTag}
          allLabel={caption}
        />
      )}

      {/*
        `hv-table-wrap` is the scroller *and* the frame: Harvester draws the
        border on the wrapper so a wide table scrolls inside its own box rather
        than pushing the page sideways.
      */}
      <div className="hv-table-wrap">
        <table className="hv-table" aria-label={caption} aria-busy={isLoading}>
          <caption className="hv-sr">{caption}</caption>
          <thead>
            <tr>
              {columns.map((column) => {
                const sortable = column.sortable !== false
                const direction = sort?.key === column.key ? sort.direction : null
                return (
                  <th
                    key={column.key}
                    scope="col"
                    style={column.width ? { width: column.width } : undefined}
                    data-align={column.align ?? 'start'}
                    aria-sort={sortable ? ariaSortFor(sort, column.key) : undefined}
                  >
                    {sortable ? (
                      <button
                        type="button"
                        className="hv-table__sort"
                        onClick={() => setSort((current) => nextSort(current, column.key))}
                      >
                        <span>{column.header}</span>
                        <span aria-hidden="true" className="hv-table__ind">
                          {sortIndicator(direction)}
                        </span>
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                )
              })}
            </tr>
          </thead>

          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={columns.length} className="hv-table__message">
                  {loadingLabel}
                </td>
              </tr>
            )}

            {!isLoading && visible.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="hv-table__message">
                  {emptyLabel}
                </td>
              </tr>
            )}

            {!isLoading &&
              visible.map((row) => (
                <tr
                  key={rowKey(row)}
                  // Harvester hangs the pointer and hover rules off this
                  // attribute rather than a class, so a row is styled by what it
                  // does rather than by a name that has to be kept in step.
                  data-activatable={onRowActivate ? '' : undefined}
                  // Reachable by keyboard when the row opens a detail view; a
                  // click-only row is invisible to anyone not using a mouse.
                  // The implicit `row` role is deliberately left alone — making
                  // it a button would destroy the table structure for screen
                  // readers, which is a worse bug than the one it solves.
                  tabIndex={onRowActivate ? 0 : undefined}
                  onClick={onRowActivate ? () => onRowActivate(row) : undefined}
                  onKeyDown={
                    onRowActivate
                      ? (event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            onRowActivate(row)
                          }
                        }
                      : undefined
                  }
                >
                  {columns.map((column) => (
                    <td key={column.key} data-align={column.align ?? 'start'}>
                      {column.render ? column.render(row) : formatCell(column.value(row))}
                    </td>
                  ))}
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (value instanceof Date) return value.toISOString()
  return String(value)
}
