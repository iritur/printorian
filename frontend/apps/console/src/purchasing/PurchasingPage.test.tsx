/**
 * The purchasing desk, asserted at the screen.
 *
 * Five claims, and each is one a glance cannot check:
 *
 * * **A consequence nothing measured draws an em dash.** Asserted here rather
 *   than in a component test, because that is exactly the gap #88 left:
 *   `FilterChips.test.tsx` proved the component honours `count: null` and
 *   nothing proved a page ever handed it one. The equivalent hole here would be
 *   a screen that computed months of cover from the row it was given.
 *
 * * **A manager without `view_financials` never asks for `/costs`.** Not "sees
 *   no prices" — never *requests* them, so the split is real in the network log
 *   too and not merely in what happens to render.
 *
 * * **The stage path comes from the server.** The pipe marks the stage the order
 *   is actually on, drawn from `stages`, not reconstructed from `status`.
 *
 * * **A lot number typed against a line is what gets sent.** It becomes
 *   `material_lots.lot_number` and is the one field on the form nothing else can
 *   reconstruct afterwards.
 *
 * * **An unknown stage renders unnamed.** A whitelist, the `DiagnosticsPanel`
 *   rule: a stage added on the server tomorrow must not render as «На складе».
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
    seen: [] as { url: string; method: string; body: unknown }[],
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    state.seen.push({
      url: String(input),
      method: init?.method ?? 'GET',
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : null,
    })
    return state.handler(String(input), init)
  }) as unknown as typeof fetch
  return state
})

import type * as UiModule from '@printorian/ui'

import { PurchasingPage } from './PurchasingPage'
import type { PurchaseOrderView, PurchasingBoard, PurchaseStatus } from './types'

const session = vi.hoisted(() => ({ permissions: ['manage_inventory', 'view_financials'] }))

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

function aBoard(overrides: Partial<PurchasingBoard> = {}): PurchasingBoard {
  return {
    at: '2026-09-05T09:00:00Z',
    reorder: [
      {
        kind: 'material',
        item_code: 'PLA-BLACK',
        item_name: 'PLA чёрный',
        remaining: '120',
        unit: 'gram',
        threshold: '400',
        // Nothing in this system decrements remaining grams, so the server
        // cannot say what running out would cost.
        consequence: { kind: 'not_measured', committed_grams: null, committed_jobs: null },
      },
    ],
    orders: [
      {
        id: 'po1',
        number: 'PO-000094',
        status: 'paid',
        supplier_code: 'FILAMENT-RU',
        supplier_name: 'ТехноПласт',
        line_count: 1,
        total_quantity: '1000',
        expected_at: '2026-09-12T09:00:00Z',
        created_at: '2026-09-01T09:00:00Z',
      },
    ],
    counts: [
      { status: 'draft', count: 0 },
      { status: 'approved', count: 0 },
      { status: 'paid', count: 1 },
      { status: 'in_transit', count: 0 },
      { status: 'receiving', count: 0 },
      { status: 'stored', count: 0 },
      { status: 'cancelled', count: 0 },
    ],
    total: 1,
    ...overrides,
  }
}

function anOrder(overrides: Partial<PurchaseOrderView> = {}): PurchaseOrderView {
  return {
    id: 'po1',
    number: 'PO-000094',
    status: 'paid',
    supplier: {
      id: 's1',
      code: 'FILAMENT-RU',
      name: 'ТехноПласт',
      kinds: ['material'],
      is_active: true,
    },
    note: null,
    expected_at: '2026-09-12T09:00:00Z',
    created_at: '2026-09-01T09:00:00Z',
    stages: [
      { status: 'draft', at: '2026-09-01T09:00:00Z', is_current: false },
      { status: 'approved', at: '2026-09-02T09:00:00Z', is_current: false },
      { status: 'paid', at: '2026-09-03T09:00:00Z', is_current: true },
      // Null, not the receiving time: an order that skipped transit has no
      // transit time, and borrowing the neighbour's would invent a day.
      { status: 'in_transit', at: null, is_current: false },
      { status: 'receiving', at: null, is_current: false },
      { status: 'stored', at: null, is_current: false },
    ],
    lines: [
      {
        id: 'l1',
        kind: 'material',
        item_code: 'PLA-BLACK',
        item_name: 'PLA чёрный',
        quantity: '1000',
        unit: 'gram',
        received_quantity: '0',
        is_receivable: true,
      },
    ],
    receipts: [],
    ...overrides,
  }
}

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

function serve(board: PurchasingBoard, order: PurchaseOrderView = anOrder()) {
  net.handler = (url: string) => {
    if (url.includes('/purchasing/board')) return Promise.resolve(jsonOk(board))
    if (url.includes('/purchasing/suppliers')) return Promise.resolve(jsonOk([]))
    if (url.includes('/costs'))
      return Promise.resolve(
        jsonOk({
          order_id: order.id,
          number: order.number,
          lines: [],
          total: null,
          unpriced_lines: 1,
          frozen_in_stock: null,
        }),
      )
    if (url.includes('/purchasing/orders/')) return Promise.resolve(jsonOk(order))
    return Promise.reject(new Error(`unexpected request: ${url}`))
  }
}

beforeEach(() => {
  session.permissions = ['manage_inventory', 'view_financials']
  net.seen.length = 0
  serve(aBoard())
})

describe('the reorder list', () => {
  it('draws an em dash where consumption was never measured', async () => {
    render(<PurchasingPage locale="ru" />)

    const row = (await screen.findByText('PLA чёрный')).closest('tr')
    expect(row).not.toBeNull()
    // The consequence cell is the last one, and it says nothing rather than a
    // month figure the farm never measured.
    const cells = within(row as HTMLElement).getAllByRole('cell')
    expect(cells[cells.length - 1]?.textContent).toBe('—')
    expect(row?.textContent).not.toMatch(/месяц/)
  })

  it('states the queued work when the server actually measured some', async () => {
    serve(
      aBoard({
        reorder: [
          {
            kind: 'material',
            item_code: 'PLA-BLACK',
            item_name: 'PLA чёрный',
            remaining: '120',
            unit: 'gram',
            threshold: '400',
            consequence: {
              kind: 'committed_work',
              committed_grams: '820',
              committed_jobs: 3,
            },
          },
        ],
      }),
    )

    render(<PurchasingPage locale="ru" />)

    const row = (await screen.findByText('PLA чёрный')).closest('tr')
    expect(row?.textContent).toMatch(/3\s*заказов ждёт/)
  })
})

describe('the money split', () => {
  it('never requests /costs for a manager without view_financials', async () => {
    session.permissions = ['manage_inventory']

    render(<PurchasingPage locale="ru" />)

    await userEvent.click(await screen.findByRole('button', { name: 'PO-000094' }))
    await screen.findByText('Путь заказа')

    expect(net.seen.some((call) => call.url.includes('/costs'))).toBe(false)
    expect(screen.queryByText('Стоимость заказа')).toBeNull()
  })

  it('asks for the prices when the actor may read money', async () => {
    render(<PurchasingPage locale="ru" />)

    await userEvent.click(await screen.findByRole('button', { name: 'PO-000094' }))

    await waitFor(() =>
      expect(net.seen.some((call) => call.url.includes('/costs'))).toBe(true),
    )
  })
})

describe('one order', () => {
  it('requests its detail and marks the stage it is on', async () => {
    const { container } = render(<PurchasingPage locale="ru" />)

    await userEvent.click(await screen.findByRole('button', { name: 'PO-000094' }))
    await screen.findByText('Путь заказа')

    expect(net.seen.some((call) => call.url.endsWith('/purchasing/orders/po1'))).toBe(true)
    const current = container.querySelector('.hv-pipe__step[data-state="now"]')
    expect(current?.textContent).toMatch(/Оплачен/)
    // A stage never entered keeps its dash rather than borrowing a neighbour's
    // timestamp.
    const steps = Array.from(container.querySelectorAll('.hv-pipe__step'))
    expect(steps[3]?.textContent).toMatch(/—/)
  })

  it('sends the lot number typed against each line', async () => {
    render(<PurchasingPage locale="ru" />)

    await userEvent.click(await screen.findByRole('button', { name: 'PO-000094' }))
    await userEvent.type(await screen.findByLabelText('Пришло — PLA чёрный'), '1000')
    await userEvent.type(screen.getByLabelText('Номер партии — PLA чёрный'), 'B-4471')
    await userEvent.click(screen.getByRole('button', { name: 'Принять' }))

    await waitFor(() =>
      expect(net.seen.some((call) => call.url.includes('/receive'))).toBe(true),
    )
    const sent = net.seen.find((call) => call.url.includes('/receive'))
    expect(sent?.body).toEqual({
      lines: [
        {
          line_id: 'l1',
          quantity: '1000',
          // Untouched fields stay null. A blank price is "nobody said", which is
          // not the same claim as free.
          unit_price_paid: null,
          lot_number: 'B-4471',
          shelf: null,
        },
      ],
    })
  })
})

describe('a stage this build has never heard of', () => {
  it('renders unnamed rather than as the last stage', async () => {
    const board = aBoard()
    // Exactly what a server one release ahead would send.
    board.orders[0]!.status = 'quarantined' as PurchaseStatus

    serve(board)
    render(<PurchasingPage locale="ru" />)

    const row = (await screen.findByRole('button', { name: 'PO-000094' })).closest('tr')
    expect(row?.textContent).toMatch(/Неизвестный этап/)
    expect(row?.textContent).not.toMatch(/На складе/)
  })
})
