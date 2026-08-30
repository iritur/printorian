/**
 * «Тарифы заказа», on the two things a glance cannot check.
 *
 * ADR-0020's guarantee was real and invisible: the rates an order was priced at
 * were pinned and stored, and no screen could show them. What is asserted here is
 * that they reach the desk, and that an order which pinned nothing says so rather
 * than rendering a table of numbers nobody was charged.
 */

import { render, screen, waitFor } from '@testing-library/react'
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

import { RateSnapshotPanel } from './RateSnapshotPanel'

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

const SNAPSHOT = {
  id: 'a1b2c3d4e5f60718293a4b5c6d7e8f90',
  engine_version: '1.4.0',
  payload: {
    schema_version: 1,
    snapshot_id: 'a1b2c3d4e5f60718293a4b5c6d7e8f90',
    margin_percent: '30',
    electricity_rate_per_kwh: '6.20',
    discounts: [
      { min_quantity: 10, percent: '5' },
      { min_quantity: 50, percent: '12.5' },
    ],
  },
  created_at: '2026-03-02T09:00:00Z',
}

beforeEach(() => {
  net.calls = []
  net.handler = (url: string) => {
    if (url.includes('/rate-snapshot')) return Promise.resolve(jsonOk(SNAPSHOT))
    return Promise.reject(new Error('unexpected request: ' + url))
  }
})

describe('the order rates panel', () => {
  it('shows the rates the order was priced at, under the label the settings screen uses', async () => {
    render(
      <RateSnapshotPanel
        order={{ id: 'o1', rate_snapshot_id: SNAPSHOT.id }}
        locale="ru"
      />,
    )

    // The backend sends keys; the client owns the words (ADR-0012), and the
    // settings catalogue already carries one for every pricing rate.
    expect(await screen.findByText('Прибыль')).toBeInTheDocument()
    expect(screen.getByText('30')).toBeInTheDocument()
    // The hash, abbreviated: two orders showing the same one were priced from
    // identical rates, which is the comparison the panel exists for.
    expect(screen.getByText(/a1b2c3d4e5f6/)).toBeInTheDocument()
  })

  it('renders the volume ladder as rungs rather than as [object Object]', async () => {
    // `discounts` is a list of objects, and it is the rate an operator is most
    // likely to be checking — a panel doing String(value) over every field would
    // print nothing readable for exactly that one.
    render(
      <RateSnapshotPanel
        order={{ id: 'o1', rate_snapshot_id: SNAPSHOT.id }}
        locale="ru"
      />,
    )

    expect(await screen.findByText(/10\+\s*→\s*5%/)).toBeInTheDocument()
    expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument()
  })

  it('says the rates were never recorded, and does not ask the server', async () => {
    // An order placed before ADR-0020 pinned nothing. "Never recorded" is a fact
    // the table already carries, so there is no request to make — and a table of
    // zeros would be a claim about rates nobody was ever charged (ADR-0007).
    render(<RateSnapshotPanel order={{ id: 'o1', rate_snapshot_id: null }} locale="ru" />)

    expect(
      await screen.findByText('Тарифы этого заказа не записаны'),
    ).toBeInTheDocument()
    await waitFor(() => {
      expect(net.calls.filter((url) => url.includes('/rate-snapshot'))).toHaveLength(0)
    })
  })
})
