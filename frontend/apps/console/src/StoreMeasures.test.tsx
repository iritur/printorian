/**
 * «Оборачиваемость» and «Залежалое» at the screen.
 *
 * * **Dead stock is never requested without `view_financials`** — not refused,
 *   not drawn with dashes: not asked for. The network log is the assertion.
 * * **A family with nothing turned yet draws a dash for its mean**, never
 *   «0.0 дн», and its waiting lots are counted beside it.
 * * **An unpriced lot draws a dash for its value**, and the footer says how many
 *   the total leaves out.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    calls: [] as string[],
    handler: (() => Promise.reject(new Error('no handler'))) as (url: string) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL) => {
    state.calls.push(String(input))
    return state.handler(String(input))
  }) as unknown as typeof fetch
  return state
})

import type * as UiModule from '@printorian/ui'

import { StoreMeasures } from './StoreMeasures'

const session = vi.hoisted(() => ({ permissions: ['view_production', 'view_financials'] }))

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

const turnover = {
  since: '2026-06-26T12:00:00Z',
  until: '2026-09-24T12:00:00Z',
  rows: [
    { family: 'PETG', turned: 0, mean_days_on_shelf: null, still_on_shelf: 3 },
    { family: 'PLA', turned: 12, mean_days_on_shelf: '6.4', still_on_shelf: 5 },
  ],
}

const dead = {
  idle_days: 60,
  lots: [
    {
      lot_id: 'l1',
      label: 'PETG-CF-004',
      family: 'PETG',
      remaining_grams: '640',
      idle_days: '93.5',
      value: '1382.40',
    },
    {
      lot_id: 'l2',
      label: 'PLA-BLACK-019',
      family: 'PLA',
      remaining_grams: '250',
      idle_days: '71.0',
      value: null,
    },
  ],
  total_grams: '890',
  total_value: '1382.40',
  unpriced_lots: 1,
}

beforeEach(() => {
  session.permissions = ['view_production', 'view_financials']
  net.calls.length = 0
  net.handler = (url: string) => {
    if (url.endsWith('/store/turnover')) return Promise.resolve(jsonOk(turnover))
    if (url.endsWith('/store/dead-stock')) return Promise.resolve(jsonOk(dead))
    return Promise.reject(new Error(`unexpected request: ${url}`))
  }
})

describe('turnover', () => {
  it('draws a dash for a family nothing has left, with its waiting lots beside it', async () => {
    render(<StoreMeasures locale="ru" />)

    const petg = (await screen.findByText('PETG', { selector: 'td' })).closest('tr')
    const cells = within(petg as HTMLElement).getAllByRole('cell')
    expect(cells[1]?.textContent).toBe('0')
    expect(cells[2]?.textContent).toBe('—')
    expect(cells[3]?.textContent).toBe('3')
    const pla = screen.getByText('PLA', { selector: 'td' }).closest('tr')
    expect(pla?.textContent).toMatch(/6\.4 дн/)
  })
})

describe('dead stock', () => {
  it('draws the value where a price was recorded and says how many lots it could not cost', async () => {
    render(<StoreMeasures locale="ru" />)

    const priced = (await screen.findByText('PETG-CF-004')).closest('tr')
    expect(priced?.textContent).toMatch(/1\s?382 ₽/)
    const unpriced = screen.getByText('PLA-BLACK-019').closest('tr')
    const cells = within(unpriced as HTMLElement).getAllByRole('cell')
    expect(cells[3]?.textContent).toBe('—')
    expect(screen.getByText(/БЕЗ ЦЕНЫ: 1/)).toBeInTheDocument()
  })

  it('is never requested by somebody who may not see money', async () => {
    session.permissions = ['view_production']

    render(<StoreMeasures locale="ru" />)

    await screen.findByText('PLA', { selector: 'td' })
    await waitFor(() => expect(net.calls.some((url) => url.endsWith('/store/turnover'))).toBe(true))
    expect(net.calls.some((url) => url.endsWith('/store/dead-stock'))).toBe(false)
    expect(screen.queryByText('Залежалое')).toBeNull()
  })
})
