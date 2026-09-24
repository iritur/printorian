/**
 * The logistics screen, asserted at the screen.
 *
 * * **A share is drawn over the promised deliveries, with the counts beside it**,
 *   and a carrier with nothing promised draws a dash — never «0%» or «100%».
 * * **A zone whose promise changed is two rows**, as the server sends them;
 *   the screen never merges them into one average.
 * * **A parcel with no zone draws no promise**, not a promise of nought days.
 * * **Recording an event sends the kind and the note to the shipment's own
 *   path**, and a packer without `pack_order` sees no controls.
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

import { LogisticsPage } from './LogisticsPage'
import type { LogisticsBoard, Scorecards, Shipment } from './types'

const session = vi.hoisted(() => ({ permissions: ['view_production', 'pack_order'] }))

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

function aShipment(overrides: Partial<Shipment> = {}): Shipment {
  return {
    id: 's1',
    order_id: 'o1',
    order_number: 'ORD-2119',
    pack_task_id: 'pk1',
    carrier_code: 'cdek',
    zone_code: 'sib',
    promised_days: 6,
    tracking_number: null,
    status: 'in_transit',
    shipped_at: '2026-09-20T09:00:00Z',
    delivered_at: null,
    returned_at: null,
    transit_days: null,
    on_time: null,
    days_out: '4.2',
    events: [
      {
        id: 'e1',
        at: '2026-09-20T09:00:00Z',
        kind: 'handed_over',
        source: 'system',
        note: null,
        recorded_by: null,
      },
      {
        id: 'e2',
        at: '2026-09-21T10:00:00Z',
        kind: 'scan',
        source: 'carrier',
        note: 'в сортировочном центре',
        recorded_by: null,
      },
    ],
    ...overrides,
  }
}

function aBoard(): LogisticsBoard {
  return {
    at: '2026-09-24T12:00:00Z',
    in_transit: [
      aShipment(),
      aShipment({
        id: 's2',
        order_number: 'ORD-2112',
        carrier_code: 'courier',
        zone_code: null,
        promised_days: null,
        days_out: '0.3',
      }),
    ],
    problems: [],
    closed: [],
    closed_since: '2026-09-23T12:00:00Z',
  }
}

function aScorecards(): Scorecards {
  return {
    since: '2026-06-26T12:00:00Z',
    until: '2026-09-24T12:00:00Z',
    carriers: [
      {
        carrier_code: 'cdek',
        shipments: 204,
        delivered: 190,
        promised: 180,
        on_time: 167,
        on_time_share: '0.9278',
        damaged: 2,
        returned: 3,
        mean_transit_days: '4.1',
      },
      {
        carrier_code: 'pickup',
        shipments: 12,
        delivered: 12,
        promised: 0,
        on_time: 0,
        on_time_share: null,
        damaged: 0,
        returned: 0,
        mean_transit_days: '0.2',
      },
    ],
    zones: [
      {
        zone_code: 'msk',
        promised_days: 1,
        delivered: 40,
        on_time: 39,
        accuracy: '0.9750',
        mean_transit_days: '0.9',
      },
      {
        zone_code: 'msk',
        promised_days: 2,
        delivered: 10,
        on_time: 10,
        accuracy: '1.0000',
        mean_transit_days: '1.1',
      },
    ],
  }
}

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

function serve(detail: Shipment = aShipment()) {
  net.handler = (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return Promise.resolve(jsonOk(detail))
    if (url.endsWith('/logistics/board')) return Promise.resolve(jsonOk(aBoard()))
    if (url.includes('/logistics/scorecards')) return Promise.resolve(jsonOk(aScorecards()))
    if (url.includes('/logistics/shipments/')) return Promise.resolve(jsonOk(detail))
    return Promise.reject(new Error(`unexpected request: ${url}`))
  }
}

beforeEach(() => {
  session.permissions = ['view_production', 'pack_order']
  net.seen.length = 0
  serve()
})

describe('the scorecards', () => {
  it('draws the share over promised deliveries with its counts, and a dash where nothing was promised', async () => {
    render(<LogisticsPage locale="ru" />)

    const cdek = (await screen.findByText('cdek', { selector: 'td' })).closest('tr')
    const cells = within(cdek as HTMLElement).getAllByRole('cell')
    expect(cells[1]?.textContent).toBe('204')
    expect(cells[2]?.textContent).toBe('93% (167 из 180)')
    expect(cells[3]?.textContent).toBe('2')

    const pickup = screen.getByText('pickup', { selector: 'td' }).closest('tr')
    const pickupCells = within(pickup as HTMLElement).getAllByRole('cell')
    expect(pickupCells[2]?.textContent).toBe('—')
    expect(pickup?.textContent).not.toMatch(/0%|100%/)
  })

  it('keeps a zone whose promise changed as two rows', async () => {
    render(<LogisticsPage locale="ru" />)

    await screen.findByText('Сроки доставки')
    const rows = screen.getAllByText('msk', { selector: 'td' }).map((cell) => cell.closest('tr'))
    expect(rows).toHaveLength(2)
    expect(rows[0]?.textContent).toMatch(/1\.0 дн/)
    expect(rows[0]?.textContent).toMatch(/98% \(39 из 40\)/)
    expect(rows[1]?.textContent).toMatch(/2\.0 дн/)
  })
})

describe('the board', () => {
  it('draws a parcel with no zone without inventing a promise', async () => {
    render(<LogisticsPage locale="ru" />)

    const card = (await screen.findByText('ORD-2112')).closest('li')
    expect(card?.textContent).toMatch(/courier/)
    expect(card?.textContent).not.toMatch(/обещано/)
    const promised = screen.getByText('ORD-2119').closest('li')
    expect(promised?.textContent).toMatch(/обещано 6 дн/)
    expect(promised?.textContent).toMatch(/в пути 4\.2 дн/)
  })

  it('records an event against the shipment and hides the form from a viewer', async () => {
    render(<LogisticsPage locale="ru" />)
    await userEvent.click(await screen.findByText('ORD-2119'))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/в сортировочном центре/)).toBeInTheDocument()
    await userEvent.selectOptions(within(dialog).getByLabelText('Событие'), 'delivered')
    await userEvent.type(within(dialog).getByLabelText('Что сказал перевозчик'), 'вручено')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Записать' }))

    await waitFor(() => {
      const post = net.seen.find(
        (call) => call.method === 'POST' && call.url.endsWith('/logistics/shipments/s1/events'),
      )
      expect(post?.body).toEqual({ kind: 'delivered', note: 'вручено' })
    })
  })

  it('shows a viewer the board and no controls', async () => {
    session.permissions = ['view_production']

    render(<LogisticsPage locale="ru" />)
    await userEvent.click(await screen.findByText('ORD-2119'))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByRole('button', { name: 'Записать' })).toBeNull()
    expect(within(dialog).queryByRole('button', { name: 'Записать трек' })).toBeNull()
  })
})
