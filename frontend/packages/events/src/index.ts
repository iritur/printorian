export { CLOSE_UNAUTHORIZED, EventStream, backoffDelay, streamUrl } from './stream'
export type { StreamOptions, StreamStatus } from './stream'

export { useLiveEvents } from './useLiveEvents'
export type { UseLiveEventsOptions } from './useLiveEvents'

export { isFleetEvent, isOrderEvent, isPaymentEvent } from './types'
export type {
  EventEnvelope,
  FleetEvent,
  LiveEvent,
  OrderEvent,
  OrderPlaced,
  OrderStatusChanged,
  PaymentEvent,
  PaymentSettled,
  PrinterRegistered,
  PrinterStateChanged,
  PrinterUnreachable,
  SlaCreditAccrued,
} from './types'
