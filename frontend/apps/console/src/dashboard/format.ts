/**
 * Turning the summary's decimal strings into things a person reads.
 *
 * Two rules run through all of it, both from the kit:
 *
 * * **Direction is not sentiment.** Spend rising is red, revenue rising is
 *   green, and the arrow is decided by the number while the colour is decided
 *   by what the number is *of*. `toneOf` is where that separation lives, so no
 *   tile can accidentally congratulate the farm on its costs.
 * * **Figures are compared, so they are grouped and tabular.** Everything goes
 *   through `Intl` with the screen's own locale rather than the browser's, so a
 *   console switched to EN stops writing Russian thousands separators.
 */

import type { Locale } from '@printorian/ui'

import type { Trend } from './types'

/** Which way a figure moved. `flat` also covers "no comparison exists". */
export type Direction = 'up' | 'down' | 'flat'

/** Whether that movement is good news. Decided by the metric, not the number. */
export type Sentiment = 'good' | 'bad' | 'flat'

/** What a rising figure means for this metric. */
export type Polarity = 'more_is_better' | 'less_is_better'

const INTL: Record<Locale, string> = { ru: 'ru-RU', en: 'en-GB' }

export function directionOf(trend: Trend): Direction {
  if (trend.change_percent === null) return 'flat'
  const change = Number(trend.change_percent)
  if (!Number.isFinite(change) || change === 0) return 'flat'
  return change > 0 ? 'up' : 'down'
}

export function toneOf(trend: Trend, polarity: Polarity): Sentiment {
  const direction = directionOf(trend)
  if (direction === 'flat') return 'flat'
  const rising = direction === 'up'
  return rising === (polarity === 'more_is_better') ? 'good' : 'bad'
}

/** The delta chip's text: `+27%`, `−11%`, or nothing when nothing is comparable. */
export function changeLabel(trend: Trend, locale: Locale): string | null {
  if (trend.change_percent === null) return null
  const change = Number(trend.change_percent)
  if (!Number.isFinite(change) || change === 0) return null
  // A real minus sign, not a hyphen: these sit in tabular figures.
  const sign = change > 0 ? '+' : '−'
  return `${sign}${formatNumber(Math.abs(change), locale, 1)}%`
}

export function formatNumber(value: number | string, locale: Locale, decimals = 0): string {
  const parsed = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(parsed)) return String(value)
  return parsed.toLocaleString(INTL[locale], {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  })
}

/**
 * Money, abbreviated only when the tile cannot hold it.
 *
 * A dashboard tile showing `1 243 900 ₽` in a display face wraps; showing
 * `1.24 млн ₽` does not, and the exact figure is one screen away in the order
 * desk. Under a hundred thousand nothing is abbreviated, because that is where
 * the rounding would start hiding a difference someone is looking for.
 */
export function formatMoneyShort(value: string, locale: Locale): { value: string; unit: string } {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return { value, unit: '₽' }
  const magnitude = Math.abs(amount)
  if (magnitude >= 1_000_000) {
    return {
      value: formatNumber(amount / 1_000_000, locale, 2),
      unit: locale === 'ru' ? 'МЛН ₽' : 'M ₽',
    }
  }
  if (magnitude >= 100_000) {
    return {
      value: formatNumber(amount / 1_000, locale, 0),
      unit: locale === 'ru' ? 'ТЫС ₽' : 'K ₽',
    }
  }
  return { value: formatNumber(amount, locale, 0), unit: '₽' }
}

/** Money in full, for the annotations under a tile where it always fits. */
export function formatMoney(value: string, locale: Locale): string {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return value
  return `${formatNumber(amount, locale, 0)} ₽`
}

export function formatGrams(value: string, locale: Locale): string {
  return formatNumber(value, locale, 0)
}

export function formatTime(iso: string, locale: Locale): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleTimeString(INTL[locale], { hour: '2-digit', minute: '2-digit' })
}

export function formatDay(iso: string, locale: Locale): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleDateString(INTL[locale], { day: '2-digit', month: '2-digit' })
}

/** A percentage as a share of a whole, clamped so a bar cannot overflow its track. */
export function percentOf(part: string | number, whole: number): number {
  const value = typeof part === 'number' ? part : Number(part)
  if (!Number.isFinite(value) || whole <= 0) return 0
  return Math.max(0, Math.min(100, (value / whole) * 100))
}
