/**
 * The shape of what `/logistics` serves, mirrored by hand — the console screens
 * type their own rows; the generated client covers the transport.
 *
 * **No money anywhere.** The kit's carrier table draws «Средняя цена» and the
 * detail draws «Что и почём»; neither exists on these routes, by design
 * (`contexts/logistics/schemas.py`). Counts, days and shares are what arrive.
 */

export type ShipmentStatus = 'handed_over' | 'in_transit' | 'problem' | 'delivered' | 'returned'

export type EventKind =
  'handed_over' | 'scan' | 'delay' | 'damaged' | 'delivered' | 'returned' | 'note'

export type EventSource = 'system' | 'person' | 'carrier'

export interface ShipmentEvent {
  id: string
  at: string
  kind: EventKind
  source: EventSource
  note: string | null
  recorded_by: string | null
}

export interface Shipment {
  id: string
  order_id: string
  order_number: string
  pack_task_id: string | null
  carrier_code: string
  /** Null is "no zone claimed the postcode" — a transit time and no promise. */
  zone_code: string | null
  promised_days: number | null
  tracking_number: string | null
  status: ShipmentStatus
  shipped_at: string
  delivered_at: string | null
  returned_at: string | null
  /** Door to door, once delivered; decimal as a string like every figure on the wire. */
  transit_days: string | null
  /** Null while out, and null for a delivery that carried no promise — not `false`. */
  on_time: boolean | null
  days_out: string
  events: ShipmentEvent[]
}

export interface LogisticsBoard {
  at: string
  in_transit: Shipment[]
  problems: Shipment[]
  closed: Shipment[]
  closed_since: string
}

/** One row of «Перевозчики». `on_time_share` is over `promised`, not over `shipments`. */
export interface CarrierScore {
  carrier_code: string
  shipments: number
  delivered: number
  promised: number
  on_time: number
  on_time_share: string | null
  damaged: number
  returned: number
  mean_transit_days: string | null
}

/** One row of «Сроки доставки», per zone *and* per pinned promise. */
export interface ZoneAccuracy {
  zone_code: string
  promised_days: number
  delivered: number
  on_time: number
  accuracy: string | null
  mean_transit_days: string | null
}

export interface Scorecards {
  since: string
  until: string
  carriers: CarrierScore[]
  zones: ZoneAccuracy[]
}

export const EVENT_KINDS: readonly EventKind[] = [
  'scan',
  'delay',
  'damaged',
  'delivered',
  'returned',
  'note',
]
