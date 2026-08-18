import { describe, expect, it } from 'vitest'

import { isFleetEvent, isOrderEvent, isPaymentEvent } from './types'
import type { LiveEvent } from './types'

/**
 * The client's event union.
 *
 * This file exists because the two sides drifted once already: the backend grew
 * to twenty-one event types and forwarded four families of them, while this
 * package still modelled five names — and nothing failed, because an unmodelled
 * event arrives as a bare `EventEnvelope` and is silently ignored. That is the
 * right *runtime* behaviour and a terrible way to notice a contract change.
 *
 * The rule is narrower than "match the backend": it is **model exactly what the
 * socket forwards**. Typing an event that `LIVE_PATTERNS` does not carry would
 * advertise a stream that never arrives; leaving out one it does carry is how a
 * screen ends up ignoring a signal it was supposed to react to.
 *
 * The half of that rule which needs to read `ws.py` is asserted from the other
 * side, in `backend/tests/api/test_events_ws.py` — this package targets the
 * browser and has no business acquiring a filesystem dependency to check a
 * contract. What is asserted *here* is that every name `MODELLED` claims does in
 * fact narrow, so the two lists cannot disagree with each other.
 */

/** Every name this package narrows to a concrete payload type. */
export const MODELLED = [
  'fleet.printer_registered',
  'fleet.printer_state_changed',
  'fleet.printer_unreachable',
  'order.placed',
  'order.status_changed',
  'order.sla_credit_accrued',
  'payment.settled',
] as const

const envelope = { event_id: 'e1', occurred_at: '2026-08-16T12:00:00+03:00' }

describe('the live event union', () => {
  it('narrows every name it claims to model', () => {
    const narrowed = MODELLED.filter((name) => {
      const event = { ...envelope, name } as LiveEvent
      return isFleetEvent(event) || isOrderEvent(event) || isPaymentEvent(event)
    })
    expect(narrowed).toEqual([...MODELLED])
  })

  it('leaves an unknown name as a weakly-typed envelope rather than dropping it', () => {
    // A name added to the backend after this build shipped. It must not narrow,
    // and it must not throw — an unrecognised event is still an invalidation
    // signal (ADR-0015), and a client that crashes on one is worse than a client
    // that refetches slightly more than it needed to.
    const future = { ...envelope, name: 'attention.raised' } as LiveEvent
    expect(isFleetEvent(future)).toBe(false)
    expect(isOrderEvent(future)).toBe(false)
    expect(isPaymentEvent(future)).toBe(false)
    expect(future.name).toBe('attention.raised')
  })

  it('models nothing the socket cannot forward', () => {
    // The wildcard families in `LIVE_PATTERNS`, restated as prefixes. The
    // authoritative direction — "every forwarded name is modelled" — is asserted
    // in `backend/tests/api/test_events_ws.py`, which can read `ws.py` directly.
    // What this catches is the cheaper mistake: modelling `job.assigned` or
    // `identity.sign_in_succeeded` and waiting forever for one to arrive.
    const forwarded = ['fleet.', 'order.', 'payment.settled', 'attention.']
    for (const name of MODELLED) {
      const carried = forwarded.some((prefix) => name.startsWith(prefix))
      expect(carried, `${name} is modelled but the socket does not forward it`).toBe(true)
    }
  })
})
