/**
 * The live event contract, mirroring `printorian.core.events`.
 *
 * Every event on the wire carries `name`, `event_id` and `occurred_at`; the rest
 * is per-event. `name` is the discriminant, so a `switch` on it narrows the
 * payload and TypeScript rejects a field the backend does not actually send.
 *
 * Names here must match the backend's `name: ClassVar[str]` exactly. The
 * contract test in `backend/tests/api/test_events_ws.py` pins the same strings
 * from the other side, so a rename that only lands on one side fails a suite.
 */

export interface EventEnvelope {
  name: string
  event_id: string
  /** ISO-8601 with an offset. Server time, not the browser's. */
  occurred_at: string
}

export interface PrinterRegistered extends EventEnvelope {
  name: 'fleet.printer_registered'
  printer_id: string
  printer_name: string
}

export interface PrinterStateChanged extends EventEnvelope {
  name: 'fleet.printer_state_changed'
  printer_id: string
  printer_name: string
  from_state: string
  to_state: string
}

export interface PrinterUnreachable extends EventEnvelope {
  name: 'fleet.printer_unreachable'
  printer_id: string
  printer_name: string
  /** An error *code*, never prose — ADR-0012. Render it through `tError`. */
  reason: string
}

export interface OrderPlaced extends EventEnvelope {
  name: 'order.placed'
  order_id: string
  number: string
  total: string
}

export interface OrderStatusChanged extends EventEnvelope {
  name: 'order.status_changed'
  order_id: string
  number: string
  from_status: string
  to_status: string
}

export interface SlaCreditAccrued extends EventEnvelope {
  name: 'order.sla_credit_accrued'
  order_id: string
  number: string
  /** Decimal string. Money is never a float on this wire. */
  credit: string
}

/**
 * The one payment event on the socket.
 *
 * `payment.started` and `payment.refunded` are emitted by the backend but are
 * **not** in `LIVE_PATTERNS`, so they never reach a client and are deliberately
 * not modelled here — a type for an event that cannot arrive is a promise this
 * package cannot keep.
 */
export interface PaymentSettled extends EventEnvelope {
  name: 'payment.settled'
  payment_id: string
  order_id: string
  amount: string
}

export type FleetEvent = PrinterRegistered | PrinterStateChanged | PrinterUnreachable
export type OrderEvent = OrderPlaced | OrderStatusChanged | SlaCreditAccrued
export type PaymentEvent = PaymentSettled

/**
 * The events this client models explicitly.
 *
 * `EventEnvelope` is deliberately part of the union: the server may broadcast a
 * name added after this build shipped, and an unknown event must arrive as a
 * weakly-typed envelope rather than crash the parser or be silently dropped.
 *
 * **This union is bounded by `LIVE_PATTERNS`, not by what the backend emits.**
 * The bus carries twenty-one event types; the socket forwards four families of
 * them (`fleet.*`, `order.*`, `payment.settled`, `attention.*`). The rest —
 * `job.*`, `plate.*`, `printer.became_free`, `identity.*` — are internal, and
 * modelling them here would advertise a stream that never arrives.
 */
export type LiveEvent = FleetEvent | OrderEvent | PaymentEvent | EventEnvelope

/** Narrowing helper — `event.name.startsWith('fleet.')` without the string math. */
export function isFleetEvent(event: LiveEvent): event is FleetEvent {
  return (
    event.name === 'fleet.printer_registered' ||
    event.name === 'fleet.printer_state_changed' ||
    event.name === 'fleet.printer_unreachable'
  )
}

export function isOrderEvent(event: LiveEvent): event is OrderEvent {
  return (
    event.name === 'order.placed' ||
    event.name === 'order.status_changed' ||
    event.name === 'order.sla_credit_accrued'
  )
}

export function isPaymentEvent(event: LiveEvent): event is PaymentEvent {
  return event.name === 'payment.settled'
}
