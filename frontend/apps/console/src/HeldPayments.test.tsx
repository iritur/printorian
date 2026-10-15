/**
 * «Оплаты на удержании», on what a person must be able to see and do.
 *
 * Security review §5(d): money for an order that was not waiting for it is held
 * for a person. The panel is that person's view, so what is asserted is that the
 * held amount and the order's state reach them, that keeping the money sends the
 * reason they wrote, that someone without `ISSUE_REFUND` is shown the money but
 * no way to keep it, and that "nothing held" is said rather than implied.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    sent: [] as { url: string; body: unknown }[],
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    state.handler(String(input), init)) as unknown as typeof fetch
  return state
})

import { HeldPayments } from './HeldPayments'

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

function aHold(overrides: Record<string, unknown> = {}) {
  return {
    payment_id: 'p1',
    order_id: 'a1b2c3d4-0000-0000-0000-000000000000',
    provider: 'yookassa',
    status: 'partially_refunded',
    amount: '1500.00',
    refunded_amount: '500.00',
    currency: 'RUB',
    settled_at: '2026-10-15T09:00:00Z',
    order_status: 'cancelled',
    held_at: '2026-10-15T09:00:00Z',
    ...overrides,
  }
}

function serve(rows: unknown[]) {
  let held = rows
  net.sent = []
  net.handler = (url: string, init?: RequestInit) => {
    if (url.includes('/release')) {
      net.sent.push({ url, body: JSON.parse(String(init?.body)) })
      held = []
      return Promise.resolve(jsonOk(aHold()))
    }
    if (url.endsWith('/payments/held')) return Promise.resolve(jsonOk(held))
    return Promise.reject(new Error('unexpected request: ' + url))
  }
}

beforeEach(() => {
  serve([aHold()])
})

describe('the held-payments panel', () => {
  it('shows what is still to decide and what the order was doing', async () => {
    render(<HeldPayments locale="ru" mayDecide={false} />)

    // 1500 taken, 500 already sent back: 1000 is what is still to decide.
    expect(await screen.findByText(/1\s*000/)).toBeInTheDocument()
    expect(screen.getByText('Отменён')).toBeInTheDocument()
    expect(screen.getByText('a1b2c3d4')).toBeInTheDocument()
  })

  it('offers no way to keep the money without the refund permission', async () => {
    render(<HeldPayments locale="ru" mayDecide={false} />)

    await screen.findByText('a1b2c3d4')
    expect(screen.queryByRole('button', { name: 'Оставить деньги' })).toBeNull()
  })

  it('keeps the money with the reason the person wrote, and the row leaves', async () => {
    render(<HeldPayments locale="ru" mayDecide={true} />)

    fireEvent.change(await screen.findByLabelText('Причина'), {
      target: { value: 'Повторный заказ по телефону' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Оставить деньги' }))

    await waitFor(() => expect(screen.getByText('Ничего не удержано')).toBeInTheDocument())
    expect(net.sent).toEqual([
      { url: '/api/payments/held/p1/release', body: { note: 'Повторный заказ по телефону' } },
    ])
  })

  it('says that nothing is held rather than drawing nothing', async () => {
    serve([])
    render(<HeldPayments locale="ru" mayDecide={true} />)

    expect(await screen.findByText('Ничего не удержано')).toBeInTheDocument()
  })
})
