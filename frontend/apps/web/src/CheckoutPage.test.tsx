/**
 * What the checkout asks the server for the price of.
 *
 * One invariant, and it is the reason `/orders/reprice` exists: the figure on
 * screen must be the figure charged. Shipping used to be one flat number, so
 * sending the delivery *method* was enough; now that a postcode changes the
 * price, a checkout that kept sending the method alone would show one number and
 * charge another — silently, and only for customers outside the flat-rate zone.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The fetch stub is installed before the imports run: `ApiClient` binds
 * `globalThis.fetch` in its constructor and the page builds one at module scope,
 * so a stub installed in `beforeEach` would arrive after the real one was
 * captured. Same reasoning as `ConfiguratorPage.test.tsx`.
 */
const net = vi.hoisted(() => {
  const state = {
    calls: [] as { url: string; body: unknown }[],
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    state.handler(String(input), init)) as unknown as typeof fetch
  return state
})

import type * as UiModule from '@printorian/ui'
import type { Breakdown } from '@printorian/ui'

import { CheckoutPage } from './CheckoutPage'
import type { Config } from './config'

vi.mock('@printorian/ui', async () => {
  const actual = await vi.importActual<typeof UiModule>('@printorian/ui')
  return {
    ...actual,
    useSession: () => ({
      ready: true,
      actor: { user_id: 'u1', email: 'buyer@example.com', permissions: ['place_order'] },
    }),
  }
})

function aBreakdown(shipping: string): Breakdown {
  return {
    currency: 'RUB',
    quantity: 1,
    total: '1000.00',
    unit_price: '1000.00',
    engine_version: '1.0.0',
    rate_snapshot_id: 'rates_abc',
    lines: [
      {
        code: 'logistics.shipping',
        category: 'logistics',
        amount: shipping,
        basis: { kind: 'flat', rate: shipping },
      },
    ],
    by_category: { logistics: shipping },
  } as Breakdown
}

const CONFIG = {
  quantity: 1,
  scale: '1',
  rush: false,
  finishes: [],
} as unknown as Config

function draw() {
  render(
    <CheckoutPage
      locale="ru"
      config={CONFIG}
      model={{
        fileName: 'bracket.stl',
        estimated_minutes: '180',
        estimated_grams: '90',
        promised_hours: '74',
      }}
      breakdown={aBreakdown('400.00')}
      materialCode="pla-black"
      colors={['black']}
      onBack={() => undefined}
      onDone={() => undefined}
    />,
  )
}

beforeEach(() => {
  net.calls = []
  net.handler = (url: string, init?: RequestInit) => {
    if (url.includes('/orders/reprice')) {
      const body = JSON.parse(String(init?.body)) as { postcode?: string }
      net.calls.push({ url, body })
      // A stand-in for the zone tariff: the flat rate until a postcode arrives,
      // the zone's once one does. Answering 550 either way would let a checkout
      // that never sends the postcode pass this file.
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({ breakdown: aBreakdown(body.postcode ? '550.00' : '400.00') }),
      } as unknown as Response)
    }
    return Promise.reject(new Error('unexpected request: ' + url))
  }
})

describe('the checkout re-price', () => {
  it('asks for a price before an address is typed', async () => {
    // The pre-address answer. A courier is picked long before an address is
    // known, and withholding the figure until one is typed is exactly what this
    // endpoint exists to avoid.
    draw()
    await userEvent.click(screen.getByRole('button', { name: 'Курьер' }))

    await waitFor(() =>
      expect(net.calls.at(-1)?.body).toMatchObject({ method: 'courier', postcode: '' }),
    )
  })

  it('sends the postcode once the customer has typed one', async () => {
    draw()
    await userEvent.click(screen.getByRole('button', { name: 'Курьер' }))
    await userEvent.type(await screen.findByLabelText('Индекс'), '300000')

    await waitFor(() =>
      expect((net.calls.at(-1)?.body as { postcode: string }).postcode).toBe('300000'),
    )
  })

  it('shows the figure the server returned, not one worked out here', async () => {
    // The configurator quoted 400 ₽; the server says 550 ₽ for this postcode.
    // A checkout that computed shipping locally would still be showing 400.
    draw()
    await userEvent.click(screen.getByRole('button', { name: 'Курьер' }))
    await userEvent.type(await screen.findByLabelText('Индекс'), '300000')

    expect(await screen.findByText(/550/)).toBeInTheDocument()
  })
})
