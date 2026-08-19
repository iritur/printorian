import type { Locale } from '@printorian/ui'
import { formatMoney } from '@printorian/ui'

/**
 * The shapes `/account` answers with, and the two formatters every panel needs.
 *
 * Hand-written rather than pulled out of the generated schema, in the same style
 * as the other storefront screens: the generated types are the *contract* check
 * (ADR-0005 — `npm run typecheck` fails when they diverge), while these are the
 * narrow slice this screen reads.
 */

export type Section = 'profile' | 'orders' | 'models' | 'addr' | 'pay' | 'notify' | 'sec'

export interface Profile {
  id: string
  email: string
  display_name: string
  role: string
  locale: string
  phone: string
  customer_kind: 'person' | 'company'
  created_at: string
}

export interface LadderStep {
  code: string
  from_spend: string
  discount_percent: string
  reached: boolean
}

export interface Tier {
  code: string
  discount_percent: string
  lifetime_spend: string
  steps: LadderStep[]
  next_code: string | null
  next_from_spend: string | null
  to_next: string | null
  progress_percent: string | null
}

export interface MonthPoint {
  month: string
  orders: number
}

export interface Lifetime {
  orders: number
  in_progress: number
  spend: string
  average_order: string | null
  saved: string
  average_days: string | null
  on_time: number
  on_time_of: number
  months: MonthPoint[]
}

export interface Overview {
  profile: Profile
  tier: Tier
  lifetime: Lifetime
}

export interface Address {
  id: string
  label: string
  recipient: string
  phone: string
  postcode: string
  city: string
  address: string
  note: string
  is_default: boolean
  created_at: string
}

export interface Notifications {
  on_paid: boolean
  on_print_started: boolean
  on_every_stage: boolean
  on_shipped: boolean
  on_new_sign_in: boolean
  on_late_credit: boolean
  journal: boolean
}

export interface ModelAsset {
  id: string
  sha256: string
  original_filename: string
  format: string
  size_bytes: number
  width_mm: string
  depth_mm: string
  height_mm: string
  is_watertight: boolean
  created_at: string | null
  last_used_at: string | null
}

export interface Shelf {
  models: { asset: ModelAsset; orders: number }[]
  used_bytes: number
  quota_bytes: number
}

export interface Receipt {
  kind: string
  order_id: string
  order_number: string
  provider: string
  amount: string
  currency: string
  issued_at: string
}

export interface SessionRow {
  id: string
  user_agent: string | null
  client_ip: string
  created_at: string
  last_seen_at: string | null
  expires_at: string
  is_current: boolean
}

/** The kit's «—». Absent is not zero, and the screen says so everywhere. */
export const NONE = '—'

/**
 * Money for the header plates: whole roubles, no kopecks.
 *
 * The KPI tiles read `186 ТЫС ₽` in the kit, which is a *scale*, not a rounding
 * — the point of that tile is the order of magnitude and kopecks would be noise
 * at that size. Exact figures still go through `formatMoney`; this is only for
 * the four plates and the tier ladder.
 */
export function roubles(amount: string | null, locale: Locale): string {
  if (amount === null) return NONE
  const value = Number(amount)
  if (!Number.isFinite(value)) return NONE
  return `${new Intl.NumberFormat(locale === 'ru' ? 'ru-RU' : 'en-GB', {
    maximumFractionDigits: 0,
  }).format(value)} ₽`
}

/** Exactly `formatMoney`, re-exported so panels import one module. */
export { formatMoney }

/**
 * A file size, in the units the kit's footer uses.
 *
 * Binary steps with the decimal names the kit writes («13.7 МБ»), which is what
 * every desktop the customer has ever used also does.
 */
export function bytes(size: number, locale: Locale): string {
  const units = locale === 'ru' ? ['Б', 'КБ', 'МБ', 'ГБ'] : ['B', 'KB', 'MB', 'GB']
  let value = size
  let step = 0
  while (value >= 1024 && step < units.length - 1) {
    value /= 1024
    step += 1
  }
  const digits = step === 0 || value >= 100 ? 0 : 1
  return `${value.toFixed(digits)} ${units[step]}`
}

/** `08.08.2026` — the kit's date, everywhere it shows one. */
export function shortDate(iso: string | null, locale: Locale): string {
  if (!iso) return NONE
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return NONE
  return at.toLocaleDateString(locale === 'ru' ? 'ru-RU' : 'en-GB', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
}

/** `вчера 21:04` for anything recent, a date for anything older. */
export function lastSeen(iso: string | null, locale: Locale): string {
  if (!iso) return NONE
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return NONE
  const tag = locale === 'ru' ? 'ru-RU' : 'en-GB'
  const minutes = (Date.now() - at.getTime()) / 60_000
  if (minutes < 5) return locale === 'ru' ? 'сейчас' : 'now'
  if (minutes < 60 * 24) {
    return at.toLocaleTimeString(tag, { hour: '2-digit', minute: '2-digit' })
  }
  return `${shortDate(iso, locale)} ${at.toLocaleTimeString(tag, {
    hour: '2-digit',
    minute: '2-digit',
  })}`
}

/** Two initials for the monogram plate — the kit has no photographs anywhere. */
export function monogram(name: string, email: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length >= 2) return (words[0]![0]! + words[1]![0]!).toUpperCase()
  if (words.length === 1 && words[0]!.length > 0) return words[0]!.slice(0, 2).toUpperCase()
  return email.slice(0, 2).toUpperCase()
}

/**
 * The device behind a user agent, in as few words as it takes to recognise it.
 *
 * Not a parser — deliberately. The security screen exists so somebody can look
 * at a row and say "that is not me", and «Chrome · Windows» does that. A full
 * UA string does not: it is long enough to push the address off the row and
 * technical enough that nobody reads it.
 */
export function device(agent: string | null): string {
  if (!agent) return NONE
  const browser =
    /Edg\//.test(agent) ? 'Edge'
    : /OPR\//.test(agent) ? 'Opera'
    : /Firefox\//.test(agent) ? 'Firefox'
    : /Chrome\//.test(agent) ? 'Chrome'
    : /Safari\//.test(agent) ? 'Safari'
    : null
  const platform =
    /Windows/.test(agent) ? 'Windows'
    : /Android/.test(agent) ? 'Android'
    : /iPhone|iPad/.test(agent) ? 'iOS'
    : /Mac OS X/.test(agent) ? 'macOS'
    : /Linux/.test(agent) ? 'Linux'
    : null
  const parts = [browser, platform].filter(Boolean)
  return parts.length ? parts.join(' · ') : agent.slice(0, 40)
}
