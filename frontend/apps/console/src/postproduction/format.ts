/**
 * Rendering minutes, paces and clocks for the post.
 *
 * One rule runs through it: **a duration an operator is judged by is written the
 * way they would say it.** "1 ч 36 м", not "96.0 минут" and not "1.6 h" — the
 * norm on the wall and the figure on the screen have to be the same sentence, or
 * the gauge stops being a gauge.
 */

import type { Locale } from '@printorian/ui'

const INTL: Record<Locale, string> = { ru: 'ru-RU', en: 'en-GB' }

/** Minutes as hours-and-minutes, or bare minutes under an hour. */
export function formatMinutes(value: string | number, locale: Locale): string {
  const total = Math.round(typeof value === 'number' ? value : Number(value))
  if (!Number.isFinite(total)) return String(value)
  const hourUnit = locale === 'ru' ? 'ч' : 'h'
  const minuteUnit = locale === 'ru' ? 'м' : 'm'
  if (total < 60) return `${total} ${minuteUnit}`
  const hours = Math.floor(total / 60)
  const minutes = total % 60
  return minutes === 0
    ? `${hours} ${hourUnit}`
    : `${hours} ${hourUnit} ${minutes} ${minuteUnit}`
}

/** `mm:ss`-style elapsed against a norm, for the detail panel's three tiles. */
export function formatStopwatch(value: string | number): string {
  const total = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(total)) return '—'
  const minutes = Math.floor(total)
  const seconds = Math.round((total - minutes) * 60)
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

export function formatClock(iso: string, locale: Locale): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleTimeString(INTL[locale], { hour: '2-digit', minute: '2-digit' })
}

export function formatDay(iso: string, locale: Locale): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleDateString(INTL[locale], { day: '2-digit', month: '2-digit' })
}

export function formatDateTime(iso: string, locale: Locale): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleString(INTL[locale], {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Which tone a pace figure gets.
 *
 * Under norm is a warning and not a failure: the screen's whole claim is that a
 * norm is a gauge, and painting an operator's row red for being 4% slow would
 * make it a stick within a week. Only a serious gap is bad.
 */
export function paceTone(pace: number | null): string | undefined {
  if (pace === null || !Number.isFinite(pace)) return undefined
  if (pace >= 100) return 'good'
  if (pace >= 85) return 'warn'
  return 'bad'
}

export function formatPercent(value: string | null, locale: Locale): string {
  if (value === null) return '—'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return value
  return `${numeric.toLocaleString(INTL[locale], { maximumFractionDigits: 1 })}%`
}
