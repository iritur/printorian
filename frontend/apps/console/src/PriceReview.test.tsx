/**
 * «Пересмотр цены», on the two claims a glance cannot check.
 *
 * The panel exists because ADR-0013's hold was invisible: every variance was
 * recorded and none was served, so the queue the policy feeds had no screen.
 * What is asserted here is that the recorded numbers reach the operator, and
 * that "measured, and there were none" does not render as a row of zeros.
 */

import { render, screen } from '@testing-library/react'
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

import { PriceReview } from './PriceReview'

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

function aVariance(overrides: Record<string, unknown> = {}) {
  return {
    id: 'v1',
    job_id: 'j1',
    order_id: 'o1',
    quoted_cost: '1000.00',
    prepared_cost: '1400.00',
    tolerance: '0.1500',
    within_tolerance: false,
    estimated_minutes: '60.00',
    prepared_minutes: '84.00',
    estimated_grams: '20.00',
    prepared_grams: '27.00',
    created_at: '2026-03-02T09:00:00Z',
    ...overrides,
  }
}

function serve(rows: unknown[]) {
  net.handler = (url: string) => {
    if (url.includes('/jobs/variances')) return Promise.resolve(jsonOk(rows))
    return Promise.reject(new Error('unexpected request: ' + url))
  }
}

const order = { id: 'o1', currency: 'RUB' }

beforeEach(() => {
  serve([aVariance()])
})

describe('the price-review panel', () => {
  it('leads with the manufacturing pair, which is what the estimator is judged on', async () => {
    render(<PriceReview order={order} locale="ru" />)

    // Estimate → plate, for both of the numbers the mesh heuristic predicts. The
    // money is those multiplied by a tariff, and only one of the two is the
    // estimator's to get wrong.
    expect(await screen.findByText(/60\.00\s*→\s*84\.00/)).toBeInTheDocument()
    expect(screen.getByText(/20\.00\s*→\s*27\.00/)).toBeInTheDocument()
    expect(screen.getByText('Задержан')).toBeInTheDocument()
  })

  it('shows a variance the farm absorbed rather than only the ones that held a job', async () => {
    // ADR-0013 keeps the in-band rows deliberately: they are the dataset Phase 6
    // calibrates against, and a panel that hid them would make the estimator
    // look worse than it is.
    serve([aVariance({ within_tolerance: true, prepared_cost: '1050.00' })])

    render(<PriceReview order={order} locale="ru" />)

    expect(await screen.findByText('В пределах допуска')).toBeInTheDocument()
  })

  it('says nothing was recorded rather than drawing zeros', async () => {
    // ADR-0007 on this screen: "measured, and there were none" is a different
    // fact from "not measured", and a row of noughts states the wrong one.
    serve([])

    render(<PriceReview order={order} locale="ru" />)

    expect(await screen.findByText('Расхождений не записано')).toBeInTheDocument()
    expect(screen.queryByText(/0,00|0\.00/)).not.toBeInTheDocument()
  })
})
