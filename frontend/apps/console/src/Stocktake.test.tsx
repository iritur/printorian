/**
 * «Инвентаризация» at the screen.
 *
 * * **An uncounted line is a dash**, never `0`.
 * * **The money is requested only behind `view_financials`** — the network log
 *   is the assertion, as in `StoreMeasures.test.tsx`.
 * * **A count posts to its own line; closing posts to `/close`.**
 * * **The tile is a dash, not `0`, when the farm has never counted.**
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    calls: [] as string[],
    posts: [] as { url: string; body: string }[],
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    state.calls.push(String(input))
    if (init?.method === 'POST') state.posts.push({ url: String(input), body: String(init.body) })
    return state.handler(String(input), init)
  }) as unknown as typeof fetch
  return state
})

import type * as UiModule from '@printorian/ui'

import { StocktakePanel, StocktakeTile } from './Stocktake'

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

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

const closed = {
  id: 'st-0',
  number: 'ST-00001',
  status: 'closed' as const,
  zone_code: null,
  opened_at: '2026-09-20T09:00:00Z',
  closed_at: '2026-09-20T12:00:00Z',
  positions: 4,
  counted: 4,
  matched: 2,
  short: 1,
  over: 1,
}

const open = {
  id: 'st-1',
  number: 'ST-00002',
  status: 'open' as const,
  zone_code: 'A',
  opened_at: '2026-09-26T09:00:00Z',
  closed_at: null,
  positions: 2,
  counted: 1,
  matched: 1,
  short: 0,
  over: 0,
}

const openDetail = {
  ...open,
  note: null,
  lines: [
    {
      lot_id: 'lot-1',
      label: 'PLA-001',
      family: 'PLA',
      cell_address: 'A1-1',
      expected_grams: '1000.00',
      counted_grams: null,
      counted_at: null,
      variance_grams: null,
    },
    {
      lot_id: 'lot-2',
      label: 'PLA-002',
      family: 'PLA',
      cell_address: 'A1-2',
      expected_grams: '500.00',
      counted_grams: '500.00',
      counted_at: '2026-09-26T10:00:00Z',
      variance_grams: null,
    },
  ],
}

const value = { id: 'st-0', number: 'ST-00001', short_value: '3240.00', over_value: '940.00', unpriced_lines: 1 }

beforeEach(() => {
  session.permissions = ['manage_inventory', 'view_financials']
  net.calls.length = 0
  net.posts.length = 0
  net.handler = (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return Promise.resolve(jsonOk(openDetail))
    if (url.endsWith('/store/stocktakes/st-1')) return Promise.resolve(jsonOk(openDetail))
    if (url.endsWith('/store/stocktakes/st-0')) return Promise.resolve(jsonOk({ ...closed, note: null, lines: [] }))
    if (url.endsWith('/store/stocktakes/st-0/value')) return Promise.resolve(jsonOk(value))
    return Promise.reject(new Error(`unexpected request: ${url}`))
  }
})

const noop = () => Promise.resolve()

describe('the tile', () => {
  it('is a dash when the farm has never counted, and the variances of the last closed count otherwise', () => {
    const { rerender } = render(<StocktakeTile history={[]} locale="ru" />)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.getByText(/ещё не было/)).toBeInTheDocument()

    // An open count first in the history has corrected nothing yet; the closed
    // one behind it is what the tile reads.
    rerender(<StocktakeTile history={[open, closed]} locale="ru" />)
    expect(screen.getByText('2')).toBeInTheDocument()
  })
})

describe('the panel', () => {
  it('draws a dash for an uncounted line and posts a count to that line', async () => {
    render(<StocktakePanel history={[open, closed]} locale="ru" mayManage onChanged={noop} />)

    const uncounted = (await screen.findByText('PLA-001')).closest('tr')
    const cells = within(uncounted as HTMLElement).getAllByRole('cell')
    expect(cells[2]?.textContent).toBe('1000')
    expect(cells[3]?.textContent).toBe('—')

    await userEvent.type(screen.getByRole('spinbutton', { name: 'PLA-001' }), '750')
    await userEvent.click(within(uncounted as HTMLElement).getByRole('button'))
    await waitFor(() =>
      expect(
        net.posts.some(
          (post) =>
            post.url.endsWith('/store/stocktakes/st-1/lines/lot-1') &&
            JSON.parse(post.body).counted_grams === '750',
        ),
      ).toBe(true),
    )

    await userEvent.click(screen.getByRole('button', { name: 'Закрыть инвентаризацию' }))
    await waitFor(() =>
      expect(net.posts.some((post) => post.url.endsWith('/store/stocktakes/st-1/close'))).toBe(true),
    )
    // An open count has no money to show, so none was asked for.
    expect(net.calls.some((url) => url.endsWith('/value'))).toBe(false)
  })

  it('shows the money of a closed count only to somebody who may see it', async () => {
    render(<StocktakePanel history={[closed]} locale="ru" mayManage onChanged={noop} />)
    expect(await screen.findByText(/3\s?240 ₽/)).toBeInTheDocument()
    expect(screen.getByText(/без цены: 1/)).toBeInTheDocument()
  })

  it('never requests the money without the permission', async () => {
    session.permissions = ['manage_inventory']
    render(<StocktakePanel history={[closed]} locale="ru" mayManage onChanged={noop} />)

    await screen.findByText('Проверено позиций')
    await waitFor(() => expect(net.calls.some((url) => url.endsWith('/store/stocktakes/st-0'))).toBe(true))
    expect(net.calls.some((url) => url.endsWith('/value'))).toBe(false)
    expect(document.body.textContent ?? '').not.toContain('₽')
  })
})
