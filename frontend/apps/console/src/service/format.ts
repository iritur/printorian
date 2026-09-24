import type { Locale, MessageKey } from '@printorian/ui'

import type { TicketKind, TicketOrigin } from './types'

/** Whitelists, the `DiagnosticsPanel` rule: a kind the server adds tomorrow renders as unknown, never as the last one this build knew. */
const KIND_KEYS: Record<TicketKind, MessageKey> = {
  install: 'svc.kind.install',
  repair: 'svc.kind.repair',
  maintenance: 'svc.kind.maintenance',
  material_load: 'svc.kind.material_load',
  move: 'svc.kind.move',
}

export function kindKey(kind: TicketKind): MessageKey {
  return KIND_KEYS[kind] ?? 'svc.kind.unknown'
}

export function originKey(origin: TicketOrigin): MessageKey {
  return origin === 'driver' ? 'svc.origin.driver' : 'svc.origin.person'
}

/**
 * Elapsed seconds as the kit's «41 М» / «4 Ч» / «1 Д 3 Ч».
 *
 * Whole minutes only: the board is refreshed on a timer, not a stopwatch, and a
 * seconds figure would claim a precision the read does not have.
 */
export function formatElapsed(seconds: number, locale: Locale): string {
  const minutes = Math.max(0, Math.floor(seconds / 60))
  const m = locale === 'ru' ? 'м' : 'm'
  const h = locale === 'ru' ? 'ч' : 'h'
  const d = locale === 'ru' ? 'д' : 'd'
  if (minutes < 60) return `${minutes} ${m}`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    const rest = minutes % 60
    return rest === 0 ? `${hours} ${h}` : `${hours} ${h} ${rest} ${m}`
  }
  const days = Math.floor(hours / 24)
  const restHours = hours % 24
  return restHours === 0 ? `${days} ${d}` : `${days} ${d} ${restHours} ${h}`
}
