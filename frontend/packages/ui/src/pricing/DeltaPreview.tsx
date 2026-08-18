import type { Locale } from '../i18n/messages'
import { translate } from '../i18n/translate'
import { formatChange, formatMoney, lineLabel } from './format'
import type { Delta } from './format'

export interface DeltaPreviewProps {
  delta: Delta
  locale: Locale
  /**
   * What the customer is pointing at, for the kit's «ПРИ ВЫБОРЕ :: ШЛИФОВКА».
   *
   * Worth the extra prop: the panel appears while the pointer is elsewhere on the
   * page, so without naming the option it describes, a customer sweeping across
   * four buttons cannot tell which of them the figures belong to.
   */
  option?: string
}

/**
 * The scenario's step 4: "+120 in labor, −260 in material" *before* committing.
 *
 * A live frame rather than a panel, which is the kit's distinction: panels hold
 * what *is*, and the live frame holds what *would be*. It is the one element on the
 * page tinted with the live accent, because it is the only one describing a
 * configuration nobody has agreed to yet.
 *
 * Shown per line rather than as a single total, because the whole point is that a
 * customer can see the trade-off an option makes — a finish that adds labour while
 * a cheaper material removes more than it costs is a decision worth understanding,
 * and a lone total hides it.
 *
 * Increases and decreases are separated so the two directions read at a glance.
 */
export function DeltaPreview({ delta, locale, option }: DeltaPreviewProps) {
  const { currency } = delta

  if (delta.changed.length === 0) {
    return (
      <p className="hv-delta hv-delta--empty hv-hint">{translate(locale, 'pricing.no_change')}</p>
    )
  }

  const increases = delta.changed.filter((line) => Number(line.change) > 0)
  const decreases = delta.changed.filter((line) => Number(line.change) < 0)
  const change = Number(delta.total_change)

  return (
    <section
      className="hv-delta hv-frame hv-frame--live"
      aria-label={translate(locale, 'pricing.what_changes')}
    >
      <div className="hv-row hv-row--between" style={{ marginBottom: 'var(--hv-2)' }}>
        <span className="hv-h hv-live">{translate(locale, 'pricing.what_changes')}</span>
        {option && (
          <span className="hv-micro">
            {translate(locale, 'pricing.on_choosing')} :: {option.toUpperCase()}
          </span>
        )}
      </div>

      {!delta.comparable && (
        <p className="hv-hint hv-warn" role="status">
          {translate(locale, 'pricing.not_comparable')}
        </p>
      )}

      <ul className="hv-leaders">
        {[...increases, ...decreases].map((line) => (
          <li
            key={line.code}
            className="hv-leader"
            // Direction *is* the tone here: a rise costs the customer money and a
            // fall saves it, which is the one thing they are reading for.
            data-tone={Number(line.change) > 0 ? 'bad' : 'good'}
            data-direction={Number(line.change) > 0 ? 'up' : 'down'}
          >
            <span className="hv-leader__k">
              {lineLabel(line.code, locale)}
              {line.is_new && (
                <span className="hv-leader__basis">{translate(locale, 'pricing.new_line')}</span>
              )}
              {line.is_removed && (
                <span className="hv-leader__basis">
                  {translate(locale, 'pricing.removed_line')}
                </span>
              )}
            </span>
            <span className="hv-leader__fill" aria-hidden="true" />
            <span className="hv-leader__v">{formatChange(line.change, currency, locale)}</span>
          </li>
        ))}
      </ul>

      <hr className="hv-hr" />

      {/*
        One figure, and it is the difference — not the before-and-after pair the
        panel used to print. The customer already has the current total in the
        breakdown below; what they came here for is what this choice costs.
      */}
      <div className="hv-slab hv-slab--outline" data-direction={change > 0 ? 'up' : 'down'}>
        <span>{translate(locale, 'pricing.total_change')}</span>
        <span className={`hv-slab__v ${change > 0 ? 'hv-bad' : 'hv-good'}`}>
          {change === 0
            ? `± ${formatMoney('0', currency, locale)}`
            : formatChange(delta.total_change, currency, locale)}
        </span>
      </div>
    </section>
  )
}
