import { translate } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { NOT_MEASURED, formatDay, formatMoney, kindKey } from './format'
import type { PositionPrice, PurchasePrices } from './types'

/**
 * «Цены по ключевым позициям» — what each position cost at receiving, over a year.
 *
 * Money, so the page asks for `/purchasing/prices` only when the actor holds
 * `view_financials`, and this panel is not drawn otherwise — the same second
 * request the order detail makes for `/costs`, and for the same reason: a
 * manager without the permission is refused the route, never shown the panel
 * with the figures blanked.
 *
 * Every figure here is picked from `purchase_receipts.unit_price_paid`. There is
 * no price table to type into, so nothing on this panel can drift from what was
 * actually paid. Two things the kit draws are therefore *not* here:
 *
 * * «БЫЛО» on a position with a single priced receipt. The server sends null,
 *   and this draws the latest price alone — one point is a price, not a stable
 *   price, and `0%` would claim a year nobody measured.
 * * The «Средневзвешенно −9% за год» slab. A weighted average of changes across
 *   filament, nozzles and boxes needs weights somebody has chosen, and nobody
 *   has.
 *
 * Receipts whose price was never recorded are counted in the footer rather than
 * averaged in as free: «3 приёмки без цены» is a fact about the paperwork, and a
 * zero in the series would be a fact about nothing.
 */
export function PriceMovements({ prices, locale }: { prices: PurchasePrices; locale: Locale }) {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)
  const unpriced = prices.positions.reduce((sum, row) => sum + row.unpriced_receipts, 0)

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('pu.prices.title')}</span>
        <span className="hv-panel__aside">{t('pu.prices.aside')}</span>
      </div>
      <div className="hv-panel__body hv-panel__body--tight">
        {prices.positions.length === 0 ? (
          <p className="hv-hint">{t('pu.prices.empty')}</p>
        ) : (
          <ul className="hv-leaders">
            {prices.positions.map((row) => (
              <li key={`${row.kind}:${row.item_code}`} className="hv-leader" data-tone={tone(row)}>
                <span className="hv-leader__k">
                  {row.item_name} · {t(kindKey(row.kind))}
                  <span className="hv-leader__basis">{basis(row, locale)}</span>
                </span>
                <i className="hv-leader__fill" />
                <span className="hv-leader__v">
                  {formatMoney(row.latest, locale)} / {row.unit} ·{' '}
                  {formatDay(row.latest_at, locale)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="hv-panel__foot">
        <span>
          {unpriced > 0 ? t('pu.prices.unpriced', { count: unpriced }) : t('pu.prices.foot')}
        </span>
      </div>
    </section>
  )
}

/**
 * The «БЫЛО» line under a position: the earlier priced receipt and the change
 * from it, or an em dash when the window holds one priced receipt.
 */
function basis(row: PositionPrice, locale: Locale): string {
  if (row.earliest === null || row.change === null) return NOT_MEASURED
  const percent = Math.round(Number(row.change) * 100)
  const sign = percent > 0 ? '+' : ''
  return translate(locale, 'pu.prices.was', {
    price: formatMoney(row.earliest, locale),
    change: `${sign}${percent}%`,
  })
}

/** Cheaper is `good`, dearer is `bad`; no second point is no tone at all. */
function tone(row: PositionPrice): 'good' | 'bad' | undefined {
  if (row.change === null) return undefined
  const change = Number(row.change)
  if (change < 0) return 'good'
  if (change > 0) return 'bad'
  return undefined
}
