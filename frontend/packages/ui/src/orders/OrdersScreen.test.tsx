/**
 * The orders table's filter chips, on the one that was missing.
 *
 * `price_review` is the only status that needs a *person* — ADR-0013 holds a job
 * there when slicing came back beyond the quote's tolerance — and it matched none
 * of the three chips the screen offered. Neither `awaiting_payment`,
 * `in_production` nor `shipped` covers it, so a held order was reachable only by
 * reading every row.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    state.handler(String(input), init)) as unknown as typeof fetch
  return state
})

vi.mock('../session/session', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('../session/session')
  return {
    ...actual,
    useSession: () => ({
      ready: true,
      actor: { user_id: 'u1', permissions: ['view_all_orders', 'manage_order'] },
    }),
  }
})

import { OrdersScreen } from './OrdersScreen'

function anOrder(number: string, status: string) {
  return {
    id: `id-${number}`,
    number,
    status,
    customer_email: 'buyer@example.com',
    currency: 'RUB',
    total: '1000.00',
    sla_credit: '0.00',
    promised_at: null,
    paid_at: null,
    created_at: '2026-03-02T09:00:00Z',
    price_breakdown: { items: [], total: '1000.00', currency: 'RUB' },
    events: [],
    allowed_transitions: [],
  }
}

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

beforeEach(() => {
  net.handler = (url: string) => {
    if (url.includes('/orders')) {
      return Promise.resolve(
        jsonOk({
          rows: [
            anOrder('PR-000001', 'price_review'),
            anOrder('PR-000002', 'printing'),
            anOrder('PR-000003', 'printing'),
          ],
          counts: [],
          total: 3,
        }),
      )
    }
    return Promise.reject(new Error('unexpected request: ' + url))
  }
})

describe('the desk filters', () => {
  it('offers a price-review chip, and it selects only the held rows', async () => {
    render(<OrdersScreen locale="ru" scope="all" title="Заказы" />)

    // The count comes from the unfiltered set, so the chip is readable before it
    // is clicked — one order is waiting on a decision.
    const chip = await screen.findByRole('button', { name: /Пересмотр\s*цены\s*1/ })
    expect(chip).toBeInTheDocument()

    await userEvent.click(chip)

    expect(screen.getByText('PR-000001')).toBeInTheDocument()
    expect(screen.queryByText('PR-000002')).not.toBeInTheDocument()
  })
})
