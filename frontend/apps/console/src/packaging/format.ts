/**
 * Rendering the packing bench's own units.
 *
 * The rule from `postproduction/format.ts` carries over — a duration somebody is
 * judged by is written the way they would say it — and two more join it here:
 *
 * **Mass is written at the scale it was measured.** 1 040 г, not 1.04 кг: the
 * scales on the bench read grams and the carrier's tariff is in kilos, and
 * silently converting between them is how a packer stops trusting either number.
 *
 * **A dash is a real value.** Every `null` on this screen means "not measured",
 * and it is drawn as «—» rather than as a zero. A cost of 0 ₽ per parcel and a
 * cost nobody has measured yet are different claims.
 */

import type { Locale } from '@printorian/ui'

const INTL: Record<Locale, string> = { ru: 'ru-RU', en: 'en-GB' }

/** Grams with a thousands separator, in the reader's own convention. */
export function formatGrams(value: string | number | null, locale: Locale): string {
  if (value === null) return '—'
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numeric)) return String(value)
  const unit = locale === 'ru' ? 'г' : 'g'
  return `${Math.round(numeric).toLocaleString(INTL[locale])} ${unit}`
}

/** A bounding box, in the order a packer reads a box's label. */
export function formatDims(length: string, width: string, height: string, locale: Locale): string {
  const parts = [length, width, height].map((one) => Math.round(Number(one)))
  if (parts.some((one) => !Number.isFinite(one))) return '—'
  const unit = locale === 'ru' ? 'мм' : 'mm'
  return `${parts.join(' × ')} ${unit}`
}

export function formatMoney(value: string | number | null, locale: Locale): string {
  if (value === null) return '—'
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numeric)) return String(value)
  return `${numeric.toLocaleString(INTL[locale], { maximumFractionDigits: 0 })} ₽`
}

/**
 * A countdown, written as the bench says it: "2 ч 14 м".
 *
 * Negative means the van has gone, and it is rendered as a magnitude with the
 * caller supplying the word — a minus sign in front of a duration reads as an
 * arithmetic result rather than as "you missed it".
 */
export function formatCountdown(value: string | number, locale: Locale): string {
  const total = Math.round(Math.abs(typeof value === 'number' ? value : Number(value)))
  if (!Number.isFinite(total)) return '—'
  const hourUnit = locale === 'ru' ? 'ч' : 'h'
  const minuteUnit = locale === 'ru' ? 'м' : 'm'
  if (total < 60) return `${total} ${minuteUnit}`
  const hours = Math.floor(total / 60)
  const minutes = total % 60
  return minutes === 0 ? `${hours} ${hourUnit}` : `${hours} ${hourUnit} ${minutes} ${minuteUnit}`
}

/**
 * Cover, written at whichever scale is still information.
 *
 * Three bands, and both edges exist for the same reason: past them the number is
 * arithmetic rather than something anybody acts on. "0.2 мес" tells a packer
 * nothing and "5 дней" tells them to order today; at the other end, a shelf with
 * two years of cover and a shelf with seventeen call for exactly the same
 * decision, and the large figure is mostly a statement about how little the farm
 * has used the item so far.
 *
 * The warning tone is a separate judgement, made from the reorder level the farm
 * actually set — see `stockTone`.
 */
export const COVER_CEILING_MONTHS = 24

export function formatCover(value: string | null, locale: Locale): string {
  if (value === null) return '—'
  const months = Number(value)
  if (!Number.isFinite(months)) return value
  const unit = locale === 'ru' ? 'мес' : 'mo'
  if (months < 1) {
    const days = Math.max(0, Math.round(months * 30))
    return locale === 'ru' ? `${days} дн.` : `${days} d`
  }
  if (months > COVER_CEILING_MONTHS) return `> ${COVER_CEILING_MONTHS} ${unit}`
  return `${months.toLocaleString(INTL[locale], { maximumFractionDigits: 1 })} ${unit}`
}

export function formatClock(iso: string | null, locale: Locale): string {
  if (iso === null) return '—'
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleTimeString(INTL[locale], { hour: '2-digit', minute: '2-digit' })
}

export function formatPercent(value: string | null, locale: Locale): string {
  if (value === null) return '—'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return value
  return `${numeric.toLocaleString(INTL[locale], { maximumFractionDigits: 1 })}%`
}

/**
 * Which tone a stock row gets.
 *
 * Out is bad, at or under the reorder level is a warning, and everything else is
 * unmarked. Deliberately driven by the level the farm set rather than by a
 * fraction of it: a threshold somebody chose is a threshold they will act on.
 */
export function stockTone(stock: string, reorderAt: string): string | undefined {
  const remaining = Number(stock)
  const threshold = Number(reorderAt)
  if (!Number.isFinite(remaining)) return undefined
  if (remaining <= 0) return 'bad'
  if (threshold > 0 && remaining <= threshold) return 'warn'
  return undefined
}
