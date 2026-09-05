/**
 * Rendering quantities, dates and stage names for the purchasing desk.
 *
 * Two rules run through it.
 *
 * **A figure the farm never measured is an em dash, never a zero.** ADR-0007 on
 * this side of the wire: `NOT_MEASURED` below is the one string every absent
 * number renders as, so a reader can tell "nothing is waiting" from "nobody
 * counted".
 *
 * **A stage this build has never heard of is named as unknown.** The label
 * lookups are whitelists keyed off the union in `types.ts` rather than string
 * interpolation into a message key, because interpolation would turn a stage
 * added on the server into a blank — or, worse, into whichever neighbouring key
 * happened to match.
 */

import type { Locale, MessageKey } from '@printorian/ui'

import type { PurchasableKind, PurchaseStatus } from './types'

const INTL: Record<Locale, string> = { ru: 'ru-RU', en: 'en-GB' }

/** Not measured. The same em dash every unmeasured figure in this app renders. */
export const NOT_MEASURED = '—'

/** Message keys for the seven stages. A whitelist — see the module docstring. */
const STATUS_KEYS: Record<PurchaseStatus, MessageKey> = {
  draft: 'pu.status.draft',
  approved: 'pu.status.approved',
  paid: 'pu.status.paid',
  in_transit: 'pu.status.in_transit',
  receiving: 'pu.status.receiving',
  stored: 'pu.status.stored',
  cancelled: 'pu.status.cancelled',
}

const KIND_KEYS: Record<PurchasableKind, MessageKey> = {
  material: 'pu.kind.material',
  printer: 'pu.kind.printer',
  spare_part: 'pu.kind.spare_part',
  packaging: 'pu.kind.packaging',
  post_consumable: 'pu.kind.post_consumable',
}

/**
 * The message key for a stage, or the "unknown" key for one this build lacks.
 *
 * `status` is typed as the union, so a *typed* caller cannot reach the fallback
 * — but the value came off the wire and TypeScript's word for that is a promise,
 * not a check. The lookup is what makes the promise true at runtime.
 */
export function statusKey(status: PurchaseStatus): MessageKey {
  return STATUS_KEYS[status] ?? 'pu.status.unknown'
}

export function kindKey(kind: PurchasableKind): MessageKey {
  return KIND_KEYS[kind] ?? 'pu.kind.unknown'
}

/**
 * Which tone a stage gets on a chip or a state tag.
 *
 * Cancelled is `bad` and stored is `good`; everything between is neutral or
 * live, because an order in transit is a fact in motion rather than a problem.
 */
export function statusTone(status: PurchaseStatus): string | undefined {
  if (status === 'cancelled') return 'bad'
  if (status === 'stored') return 'good'
  if (status === 'in_transit' || status === 'receiving') return 'live'
  return undefined
}

/**
 * A quantity with its unit, both from the server.
 *
 * The unit travels with the number rather than being assumed, because one board
 * carries grams, rolls and pieces at once — a bare "52" is the shape in which a
 * stock figure stops meaning anything.
 */
export function formatQuantity(value: string | null, unit: string, locale: Locale): string {
  if (value === null) return NOT_MEASURED
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return value
  const shown = numeric.toLocaleString(INTL[locale], { maximumFractionDigits: 2 })
  return unit ? `${shown} ${unit}` : shown
}

/** Rubles, or an em dash where the server declined to compute a total. */
export function formatMoney(value: string | null, locale: Locale): string {
  if (value === null) return NOT_MEASURED
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return value
  return `${numeric.toLocaleString(INTL[locale], { maximumFractionDigits: 2 })} ₽`
}

export function formatDay(iso: string | null, locale: Locale): string {
  if (iso === null) return NOT_MEASURED
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleDateString(INTL[locale], { day: '2-digit', month: '2-digit', year: 'numeric' })
}

export function formatStamp(iso: string | null, locale: Locale): string {
  if (iso === null) return NOT_MEASURED
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleString(INTL[locale], {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
