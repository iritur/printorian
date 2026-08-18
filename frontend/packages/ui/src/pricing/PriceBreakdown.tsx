import type { Locale, MessageKey } from '../i18n/messages'
import { translate } from '../i18n/translate'
import { formatBasis, formatMoney, lineLabel } from './format'
import type { Breakdown, BreakdownLine } from './format'

/**
 * The kit's five group headings over the engine's eight categories.
 *
 * Logistics, overheads and risk share a heading because to a customer they are one
 * question — "what else is in this?" — and five headings read where eight scroll.
 * Adjustments and margin share one for the same reason: a discount and a profit
 * line are both the farm's hand on the final number.
 *
 * Order is the order the money is spent in, which is also the order the kit prints:
 * what the part is made of, what made it, who made it, what it took to get it out
 * of the door, and what the farm did to the total afterwards.
 */
const GROUPS: { label: MessageKey; categories: string[] }[] = [
  { label: 'pricing.group.material', categories: ['material'] },
  { label: 'pricing.group.machine', categories: ['machine'] },
  { label: 'pricing.group.labor', categories: ['labor'] },
  { label: 'pricing.group.other', categories: ['logistics', 'overhead', 'risk'] },
  { label: 'pricing.group.adjustments', categories: ['adjustment', 'margin'] },
]

export interface PriceBreakdownProps {
  breakdown: Breakdown
  locale: Locale
  /** Show the per-item price. Pointless for a single item. */
  showUnitPrice?: boolean
  /**
   * Hours the farm will stand behind, for the kit's «СРОК :: 74 Ч» foot.
   *
   * Optional because the breakdown does not carry it — a lead time is not money,
   * and ADR-0002 keeps the engine to money. Omitted, the foot shows only the rate
   * snapshot rather than an invented duration.
   */
  promisedHours?: string
}

/**
 * The scenario's "transparent price structure" (step 3).
 *
 * Every line shows what it is, how it was arrived at, and how much — because a
 * total on its own invites the customer to wonder what they are paying for, and a
 * farm that cannot answer that question loses the argument.
 *
 * Grouped rather than a flat run of a dozen lines: the kit's headings are what make
 * a long list legible, and they let a reader find the one number they care about
 * without reading the rest.
 *
 * Credits (discounts) are marked as such rather than relying on a minus sign,
 * which is easy to miss in a column of numbers.
 */
export function PriceBreakdown({
  breakdown,
  locale,
  showUnitPrice = true,
  promisedHours,
}: PriceBreakdownProps) {
  const { currency } = breakdown

  /*
    Grouped from the lines themselves, so a category the engine adds later still
    appears — under `other` until it is given a heading of its own. A hardcoded
    line order would silently drop it.
  */
  const grouped = GROUPS.map((group) => ({
    label: group.label,
    lines: breakdown.lines.filter((line) => group.categories.includes(line.category)),
  })).filter((group) => group.lines.length > 0)

  const known = new Set(GROUPS.flatMap((group) => group.categories))
  const ungrouped = breakdown.lines.filter((line) => !known.has(line.category))

  return (
    <section className="hv-panel hv-price" aria-label={translate(locale, 'pricing.breakdown')}>
      <div className="hv-panel__head hv-panel__head--invert">
        <span>{translate(locale, 'pricing.breakdown')}</span>
        <span className="hv-panel__aside" style={{ color: 'inherit' }}>
          {breakdown.quantity} {translate(locale, 'unit.piece').toUpperCase()}
        </span>
      </div>

      <div className="hv-panel__body hv-panel__body--tight">
        {grouped.map((group, index) => (
          <div key={group.label}>
            <div
              className="hv-label"
              // The first heading sits tighter to the panel head than the ones
              // that follow a list of figures, as the kit spaces them.
              style={{ margin: `var(--hv-${index === 0 ? '2' : '3'}) 0 var(--hv-1)` }}
            >
              {translate(locale, group.label)}
            </div>
            <ul className="hv-leaders">
              {group.lines.map((line) => (
                <Leader key={line.code} line={line} currency={currency} locale={locale} />
              ))}
            </ul>
          </div>
        ))}

        {ungrouped.length > 0 && (
          <ul className="hv-leaders" style={{ marginTop: 'var(--hv-3)' }}>
            {ungrouped.map((line) => (
              <Leader key={line.code} line={line} currency={currency} locale={locale} />
            ))}
          </ul>
        )}
      </div>

      {/* Totals are slabs, not leaders: Harvester reserves the inverted bar for
          headline facts, and the total is the one figure a customer came for. The
          heavy rule above them is the kit's way of saying the list has ended. */}
      <div style={{ padding: '0 var(--hv-3) var(--hv-3)' }}>
        <hr className="hv-hr hv-hr--heavy" />
        <dl className="hv-price__totals">
          <div className="hv-slab hv-slab--lg">
            <dt>{translate(locale, 'pricing.total')}</dt>
            <dd className="hv-slab__v">{formatMoney(breakdown.total, currency, locale)}</dd>
          </div>
          {showUnitPrice && breakdown.quantity > 1 && (
            <div className="hv-slab hv-slab--outline" style={{ marginTop: 'var(--hv-1)' }}>
              <dt>{translate(locale, 'pricing.unit_price')}</dt>
              <dd className="hv-slab__v">
                {formatMoney(breakdown.unit_price, currency, locale)}
              </dd>
            </div>
          )}
        </dl>
      </div>

      {/*
        What this quote was priced against. The snapshot id is the reason the
        figures can be reproduced later — ADR-0020 keeps it with the order — and
        printing it here is what makes "the price is held" checkable rather than a
        claim.
      */}
      <div className="hv-panel__foot">
        {/* A lead time of zero is no promise, so it is omitted rather than printed. */}
        {Math.round(Number(promisedHours)) > 0 && (
          <span>
            {translate(locale, 'pricing.lead')} :: {Math.round(Number(promisedHours))}{' '}
            {translate(locale, 'unit.hour').toUpperCase()}
          </span>
        )}
        {/* Optional on the wire, so absent rather than «ТАРИФЫ :: UNDEFINED». */}
        {breakdown.rate_snapshot_id && (
          /*
            Abbreviated, the way a commit is shown short. The real id is a 32-hex
            digest that overflows this foot and that nobody reads in full; six
            characters is enough to tell two snapshots apart at a glance, and the
            whole value is on the element for anyone who needs it — the order keeps
            the authoritative copy either way (ADR-0020).
          */
          <span title={breakdown.rate_snapshot_id}>
            {translate(locale, 'pricing.rates')} :: {shortSnapshot(breakdown.rate_snapshot_id)}
          </span>
        )}
      </div>
    </section>
  )
}

/** `rates_a007c5f0…` → `SNAP.A007C5`, the kit's own shape. */
function shortSnapshot(id: string): string {
  const digest = id.replace(/^rates_/, '')
  return `SNAP.${digest.slice(0, 6).toUpperCase()}`
}

function Leader({
  line,
  currency,
  locale,
}: {
  line: BreakdownLine
  currency: string
  locale: Locale
}) {
  const explanation = formatBasis(line.basis, currency, locale)
  const isCredit = Number(line.amount) < 0
  return (
    <li
      className="hv-leader"
      // A credit is money coming back, so it reads in the same green as any other
      // good outcome rather than as a negative number the customer has to work out
      // the sign of.
      data-tone={isCredit ? 'good' : undefined}
      data-credit={isCredit}
    >
      <span className="hv-leader__k">
        {lineLabel(line.code, locale)}
        {explanation && <span className="hv-leader__basis">{explanation}</span>}
      </span>
      {/*
        The dotted run between label and figure. Decorative, and the reason the eye
        can follow a row across a wide panel — which is exactly why it carries no
        text for a screen reader to announce.
      */}
      <span className="hv-leader__fill" aria-hidden="true" />
      <span className="hv-leader__v">{formatMoney(line.amount, currency, locale)}</span>
    </li>
  )
}
