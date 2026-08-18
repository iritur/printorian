import { translate } from '@printorian/ui'
import type { Locale } from '@printorian/ui'

/**
 * «03 :: Размер и количество».
 *
 * Two ranges, each with a live readout above it, and the volume ladder underneath
 * the quantity.
 *
 * Scale is the control that was missing entirely before: the server has accepted
 * a `scale` field all along, so a customer could be quoted at 100% and no other
 * size. The kit's range is 50–200% in steps of 5.
 *
 * The ladder is drawn from the rate snapshot rather than assumed. Its whole point
 * is the rung *above* the current one — "five more and each is cheaper" — and a
 * farm that has configured no tiers gets no block, not an invented one.
 */

/** One rung of the volume ladder, as the quote reports it. */
export interface DiscountTier {
  min_quantity: number
  percent: string
}

export const SCALE_MIN = 50
export const SCALE_MAX = 200
export const SCALE_STEP = 5
export const QUANTITY_MAX = 50

export interface SizeStepProps {
  locale: Locale
  /** Decimal string, never a float — geometry stays exact. */
  scale: string
  quantity: number
  tiers: DiscountTier[]
  onScale: (scale: string) => void
  onQuantity: (quantity: number) => void
  onPreviewQuantity: (quantity: number) => void
  onClearPreview: () => void
}

/** `1.25` → `125`, for the slider. */
function asPercent(scale: string): number {
  return Math.round(Number(scale) * 100)
}

export function SizeStep({
  locale,
  scale,
  quantity,
  tiers,
  onScale,
  onQuantity,
  onPreviewQuantity,
  onClearPreview,
}: SizeStepProps) {
  const t = (key: Parameters<typeof translate>[1], details?: Record<string, unknown>) =>
    translate(locale, key, details)

  const percent = asPercent(scale)
  const ordered = [...tiers].sort((a, b) => a.min_quantity - b.min_quantity)
  const reached = ordered.filter((tier) => quantity >= tier.min_quantity).at(-1)
  const next = ordered.find((tier) => tier.min_quantity > quantity)

  return (
    <section className="hv-panel" onMouseLeave={onClearPreview}>
      <div className="hv-panel__head">
        <span>03 :: {t('configurator.size_and_quantity')}</span>
      </div>
      <div className="hv-panel__body hv-stack">
        <div className="hv-field">
          <div className="hv-row hv-row--between">
            <label className="hv-label" htmlFor="cfg-scale" style={{ margin: 0 }}>
              {t('configurator.scale')}
            </label>
            <span className="hv-mono">{percent}%</span>
          </div>
          <input
            className="hv-range"
            type="range"
            id="cfg-scale"
            min={SCALE_MIN}
            max={SCALE_MAX}
            step={SCALE_STEP}
            value={percent}
            /*
              Committed on release, not on every pixel of the drag. Each commit is
              a full re-upload and a fresh mesh analysis, so tracking the drag
              would fire a quote per step of the slider and the answer the customer
              wanted would arrive behind thirty they did not.
            */
            onChange={(event) => onScale(String(Number(event.target.value) / 100))}
          />
          <span className="hv-hint">{t('configurator.scale_hint')}</span>
        </div>

        <div className="hv-field">
          <div className="hv-row hv-row--between">
            <label className="hv-label" htmlFor="cfg-qty" style={{ margin: 0 }}>
              {t('configurator.quantity')}
            </label>
            <span className="hv-mono">
              {quantity} {t('unit.piece')}
            </span>
          </div>
          <input
            className="hv-range"
            type="range"
            id="cfg-qty"
            min={1}
            max={QUANTITY_MAX}
            step={1}
            value={Math.min(quantity, QUANTITY_MAX)}
            onChange={(event) => onQuantity(Number(event.target.value) || 1)}
            onMouseEnter={() => onPreviewQuantity(quantity)}
            onBlur={onClearPreview}
          />

          {/*
            The ladder. Absent when the farm has configured no tiers, because
            «Следующий порог :: 25 шт» with nothing behind it is a promise the
            checkout cannot keep.
          */}
          {(reached || next) && (
            <ul className="hv-leaders" style={{ marginTop: 'var(--hv-2)' }}>
              {reached && (
                <li className="hv-leader" data-tone="good">
                  <span className="hv-leader__k">
                    {t('configurator.volume_threshold', { count: reached.min_quantity })}
                  </span>
                  <span className="hv-leader__fill" aria-hidden="true" />
                  <span className="hv-leader__v">−{reached.percent}%</span>
                </li>
              )}
              {next && (
                <li className="hv-leader">
                  <span className="hv-leader__k">
                    {t('configurator.next_threshold', { count: next.min_quantity })}
                  </span>
                  <span className="hv-leader__fill" aria-hidden="true" />
                  <span className="hv-leader__v">−{next.percent}%</span>
                </li>
              )}
            </ul>
          )}
        </div>
      </div>
    </section>
  )
}
