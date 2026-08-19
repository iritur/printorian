import type { Locale, MessageKey } from '../i18n/messages'
import { translate } from '../i18n/translate'

/**
 * Rendering prices for humans.
 *
 * This is the payoff of ADR-0012. The backend sends `{kind, quantity, unit, rate}`
 * and never a sentence, so "4.2 ч × 400 ₽/ч" is composed *here* — which is what
 * makes a second language a catalogue change rather than a backend deployment.
 *
 * Amounts arrive as decimal **strings**, not numbers. They are parsed only at the
 * moment of display; nothing in the client does arithmetic on money, because
 * JavaScript numbers cannot represent it exactly and the server has already done
 * every calculation that matters.
 */

export type BasisKind =
  | 'flat'
  | 'per_unit'
  | 'rate_over_quantity'
  | 'percent_of'
  | 'tiered_percent'

export interface Basis {
  kind: BasisKind
  quantity: string | null
  unit: string | null
  rate: string | null
  percent: string | null
  of_codes: string[]
  tier_min_quantity: number | null
}

export interface BreakdownLine {
  code: string
  category: string
  amount: string
  basis: Basis
}

export interface Breakdown {
  currency: string
  quantity: number
  total: string
  unit_price: string
  lines: BreakdownLine[]
  by_category: Record<string, string>
  engine_version?: string
  rate_snapshot_id?: string
}

export interface DeltaLine {
  code: string
  category: string
  before: string
  after: string
  change: string
  is_new: boolean
  is_removed: boolean
}

export interface Delta {
  currency: string
  comparable: boolean
  total_before: string
  total_after: string
  total_change: string
  /**
   * The same comparison per item.
   *
   * Sent rather than derived: quantity itself can be the thing that changed, so
   * dividing the total change by the current quantity would divide by the wrong
   * number exactly when it matters. See `BreakdownDelta.unit_change`.
   */
  unit_before: string
  unit_after: string
  unit_change: string
  changed: DeltaLine[]
}

const LOCALE_TAGS: Record<Locale, string> = { ru: 'ru-RU', en: 'en-GB' }

/** Format a decimal string as currency. Never rounds — the server already did. */
export function formatMoney(amount: string, currency: string, locale: Locale): string {
  const value = Number(amount)
  if (!Number.isFinite(value)) return `${amount} ${currency}`
  return new Intl.NumberFormat(LOCALE_TAGS[locale], {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

/** Format a signed change, always showing the sign so direction is unmistakable. */
export function formatChange(amount: string, currency: string, locale: Locale): string {
  const value = Number(amount)
  const formatted = formatMoney(amount, currency, locale)
  return value > 0 ? `+${formatted}` : formatted
}

function formatNumber(value: string, locale: Locale): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return value
  // Rates and quantities carry more precision than money; trim trailing noise.
  return new Intl.NumberFormat(LOCALE_TAGS[locale], {
    maximumFractionDigits: 3,
  }).format(parsed)
}

const UNIT_KEYS: Record<string, MessageKey> = {
  hour: 'unit.hour',
  gram: 'unit.gram',
  kwh: 'unit.kwh',
  unit: 'unit.piece',
}

function unitLabel(unit: string | null, locale: Locale): string {
  if (!unit) return ''
  const key = UNIT_KEYS[unit]
  return key ? translate(locale, key) : unit
}

/**
 * Explain how a line's amount was reached.
 *
 * Returns an empty string when the basis says nothing useful, so a caller can hide
 * the row rather than print a stray dash.
 */
export function formatBasis(basis: Basis, currency: string, locale: Locale): string {
  switch (basis.kind) {
    case 'flat':
      return basis.rate ? formatMoney(basis.rate, currency, locale) : ''

    case 'per_unit': {
      if (!basis.quantity || !basis.rate) return ''
      const each = formatMoney(basis.rate, currency, locale)
      return `${formatNumber(basis.quantity, locale)} × ${each}`
    }

    case 'rate_over_quantity': {
      if (!basis.quantity || !basis.rate) return ''
      const amount = `${formatNumber(basis.quantity, locale)} ${unitLabel(basis.unit, locale)}`.trim()
      const rate = `${formatMoney(basis.rate, currency, locale)}/${unitLabel(basis.unit, locale)}`
      return `${amount} × ${rate}`
    }

    case 'percent_of':
      return basis.percent ? `${formatNumber(basis.percent, locale)}%` : ''

    case 'tiered_percent': {
      if (!basis.percent) return ''
      const percent = `${formatNumber(basis.percent, locale)}%`
      return basis.tier_min_quantity
        ? `${percent} · ${translate(locale, 'pricing.from_quantity')} ${basis.tier_min_quantity}`
        : percent
    }

    default:
      return ''
  }
}

/** Label a line, falling back to the raw code so a new one is visible, not blank. */
export function lineLabel(code: string, locale: Locale): string {
  const known = code as MessageKey
  try {
    const label = translate(locale, known)
    return label || code
  } catch {
    return code
  }
}

/**
 * A rate snapshot id, short enough for the chrome's one-line strip.
 *
 * `rates_8f41c2…` becomes `SNAP.8F41C2` — the kit's own form. Six characters
 * because the strip has to fit three of these beside a clock, and because the
 * full id is on the breakdown that owns it: this is the handle a support
 * conversation quotes, not the key anything is looked up by.
 *
 * An order placed before snapshots were persisted has no id, and gets «—»
 * rather than `SNAP.` with nothing after it.
 */
export function snapshotLabel(id: string | null | undefined): string {
  if (!id) return '—'
  return `SNAP.${id.replace(/^rates_/, '').slice(0, 6).toUpperCase()}`
}
