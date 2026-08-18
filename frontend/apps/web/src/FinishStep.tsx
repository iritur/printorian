import { formatChange, formatMoney, translate } from '@printorian/ui'
import type { Delta, Locale } from '@printorian/ui'

import { FINISHES } from './config'
import type { Config } from './config'

/**
 * «04 :: Обработка и срок» — the finishing level, and whether to rush it.
 *
 * Every button carries its own price change, at rest, as the kit draws it. That is
 * not decoration: it is the page's argument. A customer who can see that painting
 * costs 1 250 ₽ per item before clicking never has to discover it at checkout, and
 * a farm that shows the figure does not have to defend it.
 *
 * The figures come from the engine (`/pricing/preview`), one comparison per
 * option, never computed here — ADR-0002 keeps pricing to one implementation, and
 * a button that estimated its own delta would be a second one.
 *
 * A finish whose delta has not arrived shows «—». An unknown price renders as
 * unknown rather than as zero, which would read as free.
 */

/** Per-option deltas, keyed as `finish:<code>` and `rush`. */
export type OptionDeltas = Record<string, Delta | null>

export const RUSH_KEY = 'rush'
export const finishKey = (code: string) => `finish:${code}`

export interface FinishStepProps {
  locale: Locale
  config: Config
  deltas: OptionDeltas
  /** From the quote, so «± 0 ₽» is spelled in the currency the farm priced in. */
  currency: string
  /** Lead times from the quote: what is promised now, and what rush buys. */
  promisedHours: string
  rushHours: string
  onFinish: (code: string) => void
  onRush: (rush: boolean) => void
  /** `label` names the option for the live frame's «ПРИ ВЫБОРЕ ::». */
  onPreview: (change: Record<string, string | string[]>, label: string) => void
  onClearPreview: () => void
}

export function FinishStep({
  locale,
  config,
  deltas,
  currency,
  promisedHours,
  rushHours,
  onFinish,
  onRush,
  onPreview,
  onClearPreview,
}: FinishStepProps) {
  const t = (key: Parameters<typeof translate>[1], details?: Record<string, unknown>) =>
    translate(locale, key, details)

  // Both halves or neither: «СРОК 0 Ч ВМЕСТО 0 Ч» is what an unpriced quote used
  // to print, and a lead time of zero is not a faster promise — it is no promise.
  const rush = Math.round(Number(rushHours))
  const normal = Math.round(Number(promisedHours))
  const leadKnown = rush > 0 && normal > 0

  /**
   * One option's price change, per item.
   *
   * Per item because that is the number a customer weighs — the kit's «+ 340 ₽ /
   * шт» — and because the total moves with the quantity chosen in step 03, which
   * would make the same finish look different at 1 and at 50.
   */
  const unchanged = (
    <span className="hv-option__delta hv-faint">± {formatMoney('0', currency, locale)}</span>
  )

  const unknown = <span className="hv-option__delta hv-faint">—</span>

  const delta = (key: string, selected: boolean) => {
    if (selected) return unchanged
    const answer = deltas[key]
    // «—», not «± 0 ₽». An answer that has not arrived is unknown, and printing
    // zero would tell the customer the option is free.
    if (!answer) return unknown
    const change = Number(answer.unit_change)
    // A server that does not send the per-unit figure is also unknown. Without
    // this the NaN fell through to the formatter and printed «undefined RUB / шт»
    // on every button — worse than saying nothing, because it looks like a price.
    if (!Number.isFinite(change)) return unknown
    if (change === 0) return unchanged
    return (
      <span className="hv-option__delta" data-dir={change > 0 ? 'up' : 'down'}>
        {formatChange(answer.unit_change, answer.currency, locale)} / {t('unit.piece')}
      </span>
    )
  }

  return (
    <section className="hv-panel" onMouseLeave={onClearPreview}>
      <div className="hv-panel__head">
        <span>04 :: {t('configurator.finish_and_lead')}</span>
        <span className="hv-panel__aside">{t('configurator.affects_price')}</span>
      </div>
      <div className="hv-panel__body hv-stack hv-stack--2">
        {FINISHES.map((finish) => {
          const selected = config.finishes.includes(finish)
          const name = translate(
            locale,
            `postprocess.${finish}` as Parameters<typeof translate>[1],
          )
          return (
            <button
              key={finish}
              type="button"
              className="hv-option"
              aria-pressed={selected}
              onMouseEnter={() => onPreview({ to_finishes: finish }, name)}
              // Focus previews, so blur must reset — otherwise tabbing away
              // leaves the panel describing an option no longer under focus,
              // the keyboard version of the pointer bug.
              onFocus={() => onPreview({ to_finishes: finish }, name)}
              onBlur={onClearPreview}
              // Single-select: choosing a finish replaces the previous one, so
              // there is always exactly one and never a contradictory pair.
              onClick={() => onFinish(finish)}
            >
              <span className="hv-h">{name}</span>
              {delta(finishKey(finish), selected)}
            </button>
          )
        })}

        <hr className="hv-hr" />

        {/*
          Rush, with what it buys spelled out underneath. Both hours come from the
          quote: the promise is the farm's buffered lead time and the rush figure is
          the policy constant it replaces, so neither is a number this component
          invented.
        */}
        <button
          type="button"
          className="hv-option"
          aria-pressed={config.rush}
          onMouseEnter={() => onPreview({ to_rush: String(!config.rush) }, t('adjustment.rush'))}
          onFocus={() => onPreview({ to_rush: String(!config.rush) }, t('adjustment.rush'))}
          onBlur={onClearPreview}
          onClick={() => onRush(!config.rush)}
        >
          <span>
            <span className="hv-h">{t('adjustment.rush')}</span>
            {leadKnown && (
              <span className="hv-micro" style={{ display: 'block' }}>
                {t('configurator.rush_lead', { rush, normal })}
              </span>
            )}
          </span>
          {delta(RUSH_KEY, config.rush)}
        </button>
      </div>
    </section>
  )
}
