import { useEffect, useState } from 'react'

import { api, translate, useSession } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

/**
 * «Оборачиваемость» and «Залежалое» on the store screen — two reads over the
 * movement ledger, and the second is money.
 *
 * Turnover is grams and days and is asked for by anybody who can see the
 * store. Dead stock carries a value per lot, sits behind `VIEW_FINANCIALS` on
 * `/store/dead-stock`, and is **never requested** without that permission: the
 * route would refuse, and a refused request in the network log is still a
 * request for money by somebody who may not see it. The panel is simply not
 * drawn for them — not drawn with dashes, which would read as "measured, and
 * empty" (ADR-0007).
 *
 * Both figures keep their denominators in view: turnover shows how many lots
 * are still on the shelf beside the mean, and dead stock says how many lots it
 * could not cost because receiving recorded no price for them.
 */

const VIEW_FINANCIALS = 'view_financials'

/** Mirrored by hand from `contexts/inventory/store_measures.py`. */
export interface TurnoverRow {
  family: string
  turned: number
  mean_days_on_shelf: string | null
  still_on_shelf: number
}

interface TurnoverReport {
  since: string
  until: string
  rows: TurnoverRow[]
}

export interface DeadStockLot {
  lot_id: string
  label: string
  family: string
  remaining_grams: string
  idle_days: string
  value: string | null
}

interface DeadStockReport {
  idle_days: number
  lots: DeadStockLot[]
  total_grams: string
  total_value: string
  unpriced_lots: number
}

const INTL: Record<Locale, string> = { ru: 'ru-RU', en: 'en-GB' }

function days(value: string | null, locale: Locale): string {
  if (value === null) return '—'
  return `${Number(value).toFixed(1)} ${locale === 'ru' ? 'дн' : 'd'}`
}

function grams(value: string, locale: Locale): string {
  return `${Number(value).toLocaleString(INTL[locale], { maximumFractionDigits: 0 })} г`
}

function money(value: string | null, locale: Locale): string {
  if (value === null) return '—'
  return `${Number(value).toLocaleString(INTL[locale], { maximumFractionDigits: 0 })} ₽`
}

export function StoreMeasures({ locale }: { locale: Locale }) {
  const { actor } = useSession()
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)
  const maySeeMoney = actor?.permissions.includes(VIEW_FINANCIALS) ?? false

  const [turnover, setTurnover] = useState<TurnoverReport | null>(null)
  const [dead, setDead] = useState<DeadStockReport | null>(null)

  useEffect(() => {
    // Separate closures, so a refused or failing read of one leaves the other
    // standing; and the money read is not even started without the permission.
    void (async () => {
      try {
        setTurnover(await api.get<TurnoverReport>('/store/turnover'))
      } catch {
        setTurnover(null)
      }
    })()
    if (!maySeeMoney) return
    void (async () => {
      try {
        setDead(await api.get<DeadStockReport>('/store/dead-stock'))
      } catch {
        setDead(null)
      }
    })()
  }, [maySeeMoney])

  return (
    <>
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('store.turnover')}</span>
          <span className="hv-panel__aside">{t('store.turnover.aside')}</span>
        </div>
        <div className="hv-panel__body--none">
          <table className="hv-table">
            <thead>
              <tr>
                <th>{t('store.turnover.family')}</th>
                <th data-align="end">{t('store.turnover.turned')}</th>
                <th data-align="end">{t('store.turnover.days')}</th>
                <th data-align="end">{t('store.turnover.waiting')}</th>
              </tr>
            </thead>
            <tbody>
              {(turnover?.rows ?? []).map((row) => (
                <tr key={row.family}>
                  <td>{row.family}</td>
                  <td data-align="end">{row.turned}</td>
                  {/* No lot has left yet: a dash, never «0 дн». */}
                  <td data-align="end">{days(row.mean_days_on_shelf, locale)}</td>
                  <td data-align="end">{row.still_on_shelf}</td>
                </tr>
              ))}
              {(turnover?.rows ?? []).length === 0 && (
                <tr>
                  <td colSpan={4}>{turnover ? t('store.turnover.empty') : t('common.loading')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="hv-panel__foot">
          <span>{t('store.turnover.foot')}</span>
        </div>
      </section>

      {maySeeMoney && (
        <section className="hv-panel">
          <div className="hv-panel__head">
            <span>{t('store.dead')}</span>
            <span className="hv-panel__aside">
              {dead ? t('store.dead.aside', { days: dead.idle_days }) : '—'}
            </span>
          </div>
          <div className="hv-panel__body--none">
            <table className="hv-table">
              <thead>
                <tr>
                  <th>{t('store.dead.lot')}</th>
                  <th data-align="end">{t('store.dead.remaining')}</th>
                  <th data-align="end">{t('store.dead.idle')}</th>
                  <th data-align="end">{t('store.dead.value')}</th>
                </tr>
              </thead>
              <tbody>
                {(dead?.lots ?? []).map((lot) => (
                  <tr key={lot.lot_id}>
                    <td>
                      {lot.label}
                      <div className="hv-micro">{lot.family}</div>
                    </td>
                    <td data-align="end">{grams(lot.remaining_grams, locale)}</td>
                    <td data-align="end">{days(lot.idle_days, locale)}</td>
                    {/* No recorded price: a dash. Costing it at nought would shrink the total. */}
                    <td data-align="end">{money(lot.value, locale)}</td>
                  </tr>
                ))}
                {(dead?.lots ?? []).length === 0 && (
                  <tr>
                    <td colSpan={4}>{dead ? t('store.dead.empty') : t('common.loading')}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="hv-panel__foot">
            <span>
              {dead
                ? t('store.dead.foot', {
                    grams: grams(dead.total_grams, locale),
                    value: money(dead.total_value, locale),
                    unpriced: dead.unpriced_lots,
                  })
                : ''}
            </span>
          </div>
        </section>
      )}
    </>
  )
}
