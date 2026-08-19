/**
 * The pipeline, which is the tracking screen's whole argument.
 *
 * Nine stages, each dated from an event the order actually has. Two things it
 * has to get right and a browser check would not catch:
 *
 * **A skipped stage is not a pending one.** Nothing on this farm advances
 * postprocessing or quality control today — orders go from printing to packing
 * and pass over them — so a shipped order has gaps in the middle. Rendered as
 * «—» those read as "still to come" on an order that is already in the
 * customer's hands.
 *
 * **The frontier is the last stage reached, not the first gap.** Take the first
 * gap and every order the farm has ever shipped freezes at 06 with the parcel
 * delivered.
 */

import { describe, expect, it } from 'vitest'

import { overdueHours, pipeline, plural } from './cabinet'
import type { Order, OrderEvent, Progress } from './cabinet'

function event(to: string, at: string, sequence: number): OrderEvent {
  return { sequence, from_status: null, to_status: to, reason: `order.${to}`, created_at: at, details: {} }
}

function order(status: string, passed: [string, string][], extra: Partial<Order> = {}): Order {
  return {
    id: 'o1',
    number: 'PR-000101',
    status,
    currency: 'RUB',
    total: '8729.80',
    sla_credit: '0',
    payable_now: '8729.80',
    promised_at: null,
    paid_at: null,
    shipped_at: null,
    created_at: '2026-08-08T14:02:00Z',
    delivery_method: 'courier',
    delivery_city: 'Москва',
    delivery_postcode: '101000',
    delivery_address: 'ул. Мясницкая, д. 12',
    price_breakdown: { currency: 'RUB', quantity: 1, total: '8729.80', unit_price: '8729.80', lines: [], by_category: {} },
    lines: [],
    events: passed.map(([to, at], index) => event(to, at, index + 1)),
    ...extra,
  }
}

const NOTHING: Progress = { queue: null, machine: null }

describe('the pipeline', () => {
  it('leaves every stage pending for an order that has only just been placed', () => {
    const stages = pipeline(order('draft', []), NOTHING)

    expect(stages).toHaveLength(9)
    expect(stages.every((stage) => stage.state === 'pending')).toBe(true)
    expect(stages.every((stage) => stage.at === null)).toBe(true)
  })

  it('dates each stage from its own event and marks the furthest as current', () => {
    const stages = pipeline(
      order('printing', [
        ['paid', '2026-08-08T14:07:00Z'],
        ['prep', '2026-08-08T14:22:00Z'],
        ['queued', '2026-08-08T15:40:00Z'],
        ['printing', '2026-08-09T14:22:00Z'],
      ]),
      NOTHING,
    )

    expect(stages.map((stage) => stage.state)).toEqual([
      'done',
      'done',
      'done',
      // No job, so «Назначен» was never dated — and the order has gone past it.
      'skipped',
      'now',
      'pending',
      'pending',
      'pending',
      'pending',
    ])
    expect(stages[0]?.at).toBe('2026-08-08T14:07:00Z')
  })

  it('dates «Назначен» from the queue, which is where the assignment lives', () => {
    const progress: Progress = {
      queue: {
        job_status: 'printing',
        position: null,
        reason: null,
        predicted_start: null,
        progress_percent: 63,
        attempt: 1,
        printer_id: 'p1',
        assigned_at: '2026-08-09T14:16:00Z',
        started_at: '2026-08-09T14:22:00Z',
      },
      machine: null,
    }

    const stages = pipeline(
      order('printing', [
        ['paid', '2026-08-08T14:07:00Z'],
        ['queued', '2026-08-08T15:40:00Z'],
        ['printing', '2026-08-09T14:22:00Z'],
      ]),
      progress,
    )

    expect(stages[3]?.state).toBe('done')
    expect(stages[3]?.at).toBe('2026-08-09T14:16:00Z')
  })

  it('marks the stages a shipped order passed over as skipped, not pending', () => {
    const stages = pipeline(
      order('shipped', [
        ['paid', '2026-08-08T14:07:00Z'],
        ['prep', '2026-08-08T14:22:00Z'],
        ['queued', '2026-08-08T15:40:00Z'],
        ['printing', '2026-08-09T14:22:00Z'],
        ['packing', '2026-08-10T09:00:00Z'],
        ['shipped', '2026-08-10T11:30:00Z'],
      ]),
      NOTHING,
    )

    // 06 postprocessing and 07 quality control: never recorded, and the order
    // has left the building.
    expect(stages[5]?.state).toBe('skipped')
    expect(stages[6]?.state).toBe('skipped')
    // …and the frontier is the last stage reached, not the first gap.
    expect(stages[8]?.state).toBe('now')
    expect(stages[8]?.at).toBe('2026-08-10T11:30:00Z')
  })
})

describe('lateness', () => {
  const promised = '2026-08-11T18:00:00Z'

  it('is nothing while there is still time', () => {
    const still = order('printing', [], { promised_at: promised })
    expect(overdueHours(still, new Date('2026-08-11T12:00:00Z'))).toBeNull()
  })

  it('counts from the promise once it has passed', () => {
    const late = order('printing', [], { promised_at: promised })
    expect(overdueHours(late, new Date('2026-08-12T00:00:00Z'))).toBe(6)
  })

  it('stops when the parcel leaves, rather than growing forever', () => {
    const gone = order('shipped', [], {
      promised_at: promised,
      shipped_at: '2026-08-12T00:00:00Z',
    })
    expect(overdueHours(gone, new Date('2026-09-01T00:00:00Z'))).toBe(6)
  })

  it('does not apply to an order nobody is working on', () => {
    // «0 ч» on a cancelled order reads as "delivered on time"; the figure would
    // otherwise climb beside a credit that is correctly nought.
    const stopped = order('cancelled', [], { promised_at: promised })
    expect(overdueHours(stopped, new Date('2026-09-01T00:00:00Z'))).toBeNull()
  })
})

describe('Russian plurals', () => {
  it('uses three forms, because two produce «4 СОБЫТИЙ»', () => {
    const форма = (n: number) => plural(n, 'событие', 'события', 'событий')
    expect([1, 2, 4, 5, 11, 14, 21, 22].map(форма)).toEqual([
      'событие',
      'события',
      'события',
      'событий',
      // The teens are the trap: 11 and 14 take the many form despite ending in
      // 1 and 4.
      'событий',
      'событий',
      'событие',
      'события',
    ])
  })
})
