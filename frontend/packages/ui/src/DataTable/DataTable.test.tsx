import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { DataTable } from './DataTable'
import type { Column, StatusTag } from './types'

interface Material {
  id: string
  name: string
  status: 'stock' | 'in_printer' | 'ordered' | 'none'
  remainingGrams: number | null
}

const materials: Material[] = [
  { id: '1', name: 'PLA Чёрный', status: 'in_printer', remainingGrams: 740 },
  { id: '2', name: 'PETG Белый', status: 'stock', remainingGrams: 1000 },
  { id: '3', name: 'ABS Красный', status: 'ordered', remainingGrams: null },
  { id: '4', name: 'PLA Синий', status: 'stock', remainingGrams: 120 },
]

const columns: Column<Material>[] = [
  { key: 'name', header: 'Название', value: (row) => row.name },
  { key: 'status', header: 'Статус', value: (row) => row.status },
  {
    key: 'remaining',
    header: 'Остаток',
    value: (row) => row.remainingGrams,
    align: 'end',
  },
]

const tags: StatusTag<Material>[] = [
  { key: 'stock', label: 'На складе', match: (r) => r.status === 'stock', tone: 'good' },
  { key: 'in_printer', label: 'В принтере', match: (r) => r.status === 'in_printer' },
  { key: 'ordered', label: 'Заказан', match: (r) => r.status === 'ordered', tone: 'warn' },
]

function renderTable(overrides: Partial<Parameters<typeof DataTable<Material>>[0]> = {}) {
  return render(
    <DataTable<Material>
      rows={materials}
      columns={columns}
      rowKey={(row) => row.id}
      statusTags={tags}
      caption="Материалы"
      {...overrides}
    />,
  )
}

function bodyRowNames(): string[] {
  const rows = screen.getAllByRole('row').slice(1) // drop the header row
  return rows.map((row) => within(row).getAllByRole('cell')[0]?.textContent ?? '')
}

describe('DataTable', () => {
  it('renders every row by default', () => {
    renderTable()
    expect(bodyRowNames()).toHaveLength(materials.length)
  })

  /*
    `\s*` between the label and the count, throughout: the chip renders them as
    two adjacent spans, and whether the accessible name has a space between them
    is decided by their computed `display` — which in jsdom depends on how much
    of the UA stylesheet it implements. The component's contract is "this chip
    says this label and this many rows"; the separator is not part of it, and a
    test that pins it fails on an environment difference rather than a defect.
  */
  it('shows a count on each status tag, and a total', () => {
    renderTable()
    expect(screen.getByRole('button', { name: /На складе\s*2/ })).toBeDefined()
    expect(screen.getByRole('button', { name: /В принтере\s*1/ })).toBeDefined()
    expect(screen.getByRole('button', { name: /Материалы\s*4/ })).toBeDefined()
  })

  it('filters when a status tag is pressed, and clears when pressed again', async () => {
    const user = userEvent.setup()
    renderTable()

    await user.click(screen.getByRole('button', { name: /На складе\s*2/ }))
    expect(bodyRowNames()).toHaveLength(2)

    await user.click(screen.getByRole('button', { name: /На складе\s*2/ }))
    expect(bodyRowNames()).toHaveLength(4)
  })

  it('keeps tag counts stable while a filter is active', async () => {
    const user = userEvent.setup()
    renderTable()

    await user.click(screen.getByRole('button', { name: /Заказан\s*1/ }))
    // Counts describe the whole set, not the filtered view.
    expect(screen.getByRole('button', { name: /На складе\s*2/ })).toBeDefined()
  })

  it('cycles a header through ascending, descending, then unsorted', async () => {
    const user = userEvent.setup()
    renderTable()
    const header = screen.getByRole('button', { name: /Название/ })

    await user.click(header)
    expect(bodyRowNames()[0]).toBe('ABS Красный')

    await user.click(header)
    expect(bodyRowNames()[0]).toBe('PLA Чёрный')

    await user.click(header)
    expect(bodyRowNames()).toEqual(materials.map((m) => m.name))
  })

  it('exposes sort state to assistive technology', async () => {
    const user = userEvent.setup()
    renderTable()

    const columnHeader = screen.getByRole('columnheader', { name: /Название/ })
    expect(columnHeader.getAttribute('aria-sort')).toBe('none')

    await user.click(screen.getByRole('button', { name: /Название/ }))
    expect(columnHeader.getAttribute('aria-sort')).toBe('ascending')
  })

  it('sorts missing values last in both directions', async () => {
    const user = userEvent.setup()
    renderTable()
    const header = screen.getByRole('button', { name: /Остаток/ })

    await user.click(header)
    expect(bodyRowNames().at(-1)).toBe('ABS Красный')

    await user.click(header)
    expect(bodyRowNames().at(-1)).toBe('ABS Красный')
  })

  it('sorts numerically, not lexicographically', async () => {
    const user = userEvent.setup()
    renderTable()

    await user.click(screen.getByRole('button', { name: /Остаток/ }))
    expect(bodyRowNames().slice(0, 3)).toEqual(['PLA Синий', 'PLA Чёрный', 'PETG Белый'])
  })

  it('opens the detail view on click and on keyboard activation', async () => {
    const user = userEvent.setup()
    const onRowActivate = vi.fn()
    renderTable({ onRowActivate })

    const rows = screen.getAllByRole('row').slice(1)
    await user.click(rows[0]!)
    expect(onRowActivate).toHaveBeenCalledTimes(1)

    rows[1]!.focus()
    await user.keyboard('{Enter}')
    expect(onRowActivate).toHaveBeenCalledTimes(2)
  })

  it('renders placeholders for empty and loading states', () => {
    const { rerender } = render(
      <DataTable<Material>
        rows={[]}
        columns={columns}
        rowKey={(row) => row.id}
        caption="Материалы"
        emptyLabel="Нет записей"
      />,
    )
    expect(screen.getByText('Нет записей')).toBeDefined()

    rerender(
      <DataTable<Material>
        rows={[]}
        columns={columns}
        rowKey={(row) => row.id}
        caption="Материалы"
        isLoading
        loadingLabel="Загрузка…"
      />,
    )
    expect(screen.getByText('Загрузка…')).toBeDefined()
  })

  it('shows a dash for missing cell values rather than blank', () => {
    renderTable()
    const abs = screen.getAllByRole('row').find((row) => row.textContent?.includes('ABS'))
    expect(within(abs!).getAllByRole('cell')[2]?.textContent).toBe('—')
  })
})
