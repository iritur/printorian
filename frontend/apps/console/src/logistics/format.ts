import type { Locale, MessageKey } from '@printorian/ui'

import type { EventKind, EventSource, ShipmentStatus } from './types'

/** Whitelists, the `DiagnosticsPanel` rule: a value the server adds tomorrow renders as unknown. */
const STATUS_KEYS: Record<ShipmentStatus, MessageKey> = {
  handed_over: 'lg.status.handed_over',
  in_transit: 'lg.status.in_transit',
  problem: 'lg.status.problem',
  delivered: 'lg.status.delivered',
  returned: 'lg.status.returned',
}

const KIND_KEYS: Record<EventKind, MessageKey> = {
  handed_over: 'lg.event.handed_over',
  scan: 'lg.event.scan',
  delay: 'lg.event.delay',
  damaged: 'lg.event.damaged',
  delivered: 'lg.event.delivered',
  returned: 'lg.event.returned',
  note: 'lg.event.note',
}

const SOURCE_KEYS: Record<EventSource, MessageKey> = {
  system: 'lg.source.system',
  person: 'lg.source.person',
  carrier: 'lg.source.carrier',
}

export function statusKey(status: ShipmentStatus): MessageKey {
  return STATUS_KEYS[status] ?? 'lg.status.unknown'
}

export function kindKey(kind: EventKind): MessageKey {
  return KIND_KEYS[kind] ?? 'lg.event.unknown'
}

export function sourceKey(source: EventSource): MessageKey {
  return SOURCE_KEYS[source] ?? 'lg.source.unknown'
}

/** A share on the wire («0.9412») as «94%», or an em dash for null — never «0%». */
export function formatShare(value: string | null): string {
  if (value === null) return '—'
  return `${Math.round(Number(value) * 100)}%`
}

/** Days to one place as the kit writes them — «2.6 дн» — or an em dash for null. */
export function formatDays(value: string | number | null, locale: Locale): string {
  if (value === null) return '—'
  const unit = locale === 'ru' ? 'дн' : 'd'
  return `${Number(value).toFixed(1)} ${unit}`
}

const INTL: Record<Locale, string> = { ru: 'ru-RU', en: 'en-GB' }

export function formatStamp(iso: string | null, locale: Locale): string {
  if (iso === null) return '—'
  return new Date(iso).toLocaleString(INTL[locale], {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
