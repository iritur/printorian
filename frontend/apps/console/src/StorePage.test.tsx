/**
 * The store screen, asserted at the screen rather than at a component.
 *
 * HANDOFF records the #88 case that makes this distinction worth the file:
 * `FilterChips` proved the *component* honoured a null, and nothing proved the
 * page ever passed one. So these mount `StorePage` against a stubbed network and
 * read what a person would see.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    calls: [] as string[],
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    state.calls.push(String(input))
    return state.handler(String(input), init)
  }) as unknown as typeof fetch
  return state
})

import type * as UiModule from '@printorian/ui'

import { StorePage } from './StorePage'

const session = vi.hoisted(() => ({ permissions: ['manage_inventory'] as string[] }))

vi.mock('@printorian/ui', async () => {
  const actual = await vi.importActual<typeof UiModule>('@printorian/ui')
  return {
    ...actual,
    useSession: () => ({
      ready: true,
      actor: { user_id: 'u1', permissions: session.permissions },
    }),
  }
})

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

/**
 * Zone A holds one cell nobody declared a capacity for and one that was declared.
 * Zone Z holds nothing at all — the two ADR-0007 cases the screen must not round
 * into a percentage.
 */
// Named rather than written inline inside `map`, because `detail` below needs the
// very same cell and `map.zones[0].cells[0]` is two indexed reads that
// `noUncheckedIndexedAccess` will not let a test assume are populated.
const cellA1 = {
  id: 'c-1',
  address: 'A1-1',
  zone_code: 'A',
  capacity_lots: null,
  lot_count: 1,
  fill_percent: null,
  is_active: true,
}

const cellA2 = {
  id: 'c-2',
  address: 'A1-2',
  zone_code: 'A',
  capacity_lots: 4,
  lot_count: 2,
  fill_percent: '50.0',
  is_active: true,
}

const map = {
  zones: [
    {
      id: 'z-a',
      code: 'A',
      name: 'Зона A',
      temp_c: '21.0',
      humidity_percent: null,
      cell_count: 2,
      occupied_cells: 1,
      fill_percent: '50.0',
      cells: [cellA1, cellA2],
    },
    {
      id: 'z-z',
      code: 'Z',
      name: 'Новая',
      temp_c: null,
      humidity_percent: null,
      cell_count: 0,
      occupied_cells: 0,
      fill_percent: null,
      cells: [],
    },
  ],
  cells_total: 2,
  occupied_total: 1,
}

const movements = [
  {
    id: 'mv-1',
    lot_id: 'lot-1',
    sequence: 1,
    reason: 'stock.moved',
    grams: '0.00',
    remaining_after: '1000.00',
    at: '2026-03-02T09:00:00Z',
    actor_id: 'u1',
    from_kind: 'stock',
    from_address: null,
    to_kind: 'stock',
    to_address: 'A1-1',
    note: null,
  },
]

const detail = {
  cell: cellA1,
  lots: [
    {
      id: 'lot-1',
      label: 'PLA-001',
      remaining_grams: '1000.00',
      location_kind: 'stock',
      cell: 'A1-1',
      shelf: null,
    },
  ],
  movements,
}

beforeEach(() => {
  net.calls = []
  session.permissions = ['manage_inventory']
  net.handler = (url: string) => {
    if (url.endsWith('/store/cells')) return Promise.resolve(jsonOk(map))
    if (url.endsWith('/store/movements')) return Promise.resolve(jsonOk(movements))
    if (url.endsWith('/store/cells/A1-1')) return Promise.resolve(jsonOk(detail))
    return Promise.reject(new Error('unexpected request: ' + url))
  }
})

describe('the store screen', () => {
  it('draws no fill bar for a cell with no declared capacity', async () => {
    render(<StorePage locale="ru" />)

    const undeclared = await screen.findByRole('button', { name: /A1-1/ })
    const declared = await screen.findByRole('button', { name: /A1-2/ })

    // The declared one has a bar, which is what makes the absence below mean
    // something rather than being a selector that never matches anything.
    expect(declared.querySelector('.hv-node__fill')).not.toBeNull()
    expect(undeclared.querySelector('.hv-node__fill')).toBeNull()
    // And no zero anywhere on the cell that was never measured: a `0%` here is
    // the invented number, drawn over a cell that is actually holding a spool.
    expect(undeclared.textContent).not.toContain('0%')
  })

  it('gives a zone with no cells its own sentence rather than zero per cent', async () => {
    render(<StorePage locale="ru" />)

    // jsdom decides whether adjacent inline elements join with a space, so this
    // matches the phrase rather than an assembled accessible name.
    expect(
      await screen.findByText(/ни одной ячейки/),
    ).toBeInTheDocument()
  })

  it('opens a cell into that cell’s own detail route', async () => {
    render(<StorePage locale="ru" />)

    const cell = await screen.findByRole('button', { name: /A1-1/ })
    expect(net.calls.some((url) => url.endsWith('/store/cells/A1-1'))).toBe(false)

    await userEvent.click(cell)

    await waitFor(() =>
      expect(net.calls.some((url) => url.endsWith('/store/cells/A1-1'))).toBe(true),
    )
    // The response reaches the window: the lot label is in the detail payload and
    // in no part of the map above it.
    expect(await screen.findByText('PLA-001')).toBeInTheDocument()
  })

  it('renders no money at all', async () => {
    render(<StorePage locale="ru" />)
    await screen.findByRole('button', { name: /A1-1/ })

    // The screen-side mirror of `test_the_cell_map_carries_no_money`. The kit
    // draws «Стоимость остатков» and «Залежалое» here and both are unmeasurable
    // today, so a rouble sign on this screen is a number the farm never took.
    expect(document.body.textContent ?? '').not.toContain('₽')
  })
})
