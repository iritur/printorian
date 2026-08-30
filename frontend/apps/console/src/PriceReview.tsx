import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, formatMoney, translate, translateError } from '@printorian/ui'

/**
 * «Пересмотр цены» — what slicing found, against what the customer was quoted.
 *
 * ADR-0013 makes this a policy rather than a statistic: a plate whose prepared
 * cost exceeds the quote beyond tolerance holds the job at `price_review` instead
 * of dispatching it. Every variance was being recorded and none was being shown,
 * so the mechanism that stops a mis-estimated plate from printing at a losing
 * price was half-built — the detection worked and the queue it feeds was
 * invisible.
 *
 * **The manufacturing pair leads, and the money follows it.** Minutes and grams
 * are what the mesh estimator actually predicts; the money is that prediction
 * multiplied by a tariff, and only one of the two is the estimator's to get
 * wrong. An operator reading this row wants to know whether the part got bigger
 * or the rates moved, and the columns are ordered to answer that first.
 *
 * **In-band variances are shown too.** They are not noise: ADR-0013 keeps them
 * deliberately, because they are the farm absorbing small differences and the
 * dataset ROADMAP Phase 6 calibrates the estimator against. A panel that showed
 * only the escalations would make the estimator look worse than it is.
 */

export const VIEW_FINANCIALS = 'view_financials'

interface Variance {
  id: string
  job_id: string
  order_id: string
  quoted_cost: string
  prepared_cost: string
  tolerance: string
  within_tolerance: boolean
  estimated_minutes: string
  prepared_minutes: string
  estimated_grams: string
  prepared_grams: string
  created_at: string
}

/** A signed delta, formatted so the sign is the first thing read. */
function delta(before: string, after: string, format: (value: string) => string): string {
  const difference = Number(after) - Number(before)
  const sign = difference > 0 ? '+' : ''
  return `${sign}${format(String(difference))}`
}

export function PriceReview({
  order,
  locale,
}: {
  order: { id: string; currency: string }
  locale: Locale
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const [rows, setRows] = useState<Variance[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await api.get<Variance[]>(`/jobs/variances?order_id=${order.id}`))
      setError(null)
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
    }
  }, [order.id, locale])

  useEffect(() => {
    void (async () => {
      await load()
    })()
  }, [load])

  if (error) return <p className="hv-faint">{error}</p>
  // Null is "not loaded yet" and `[]` is "measured, and there were none". They
  // must not render the same thing (ADR-0007).
  if (rows === null) return null

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('variance.title')}</span>
        <span className="hv-panel__aside">{rows.length}</span>
      </div>
      <div className="hv-panel__body--none">
        <div className="hv-table-wrap">
          <table className="hv-table">
            <thead>
              <tr>
                <th>{t('variance.minutes')}</th>
                <th>{t('variance.grams')}</th>
                <th data-align="end">{t('variance.quoted')}</th>
                <th data-align="end">{t('variance.prepared')}</th>
                <th data-align="end">{t('variance.delta')}</th>
                <th>{t('variance.verdict')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    {row.estimated_minutes} → {row.prepared_minutes}
                  </td>
                  <td>
                    {row.estimated_grams} → {row.prepared_grams}
                  </td>
                  <td data-align="end">{formatMoney(row.quoted_cost, order.currency, locale)}</td>
                  <td data-align="end">{formatMoney(row.prepared_cost, order.currency, locale)}</td>
                  <td data-align="end" data-sentiment={row.within_tolerance ? undefined : 'bad'}>
                    {delta(row.quoted_cost, row.prepared_cost, (value) =>
                      formatMoney(value, order.currency, locale),
                    )}
                  </td>
                  <td>{row.within_tolerance ? t('variance.absorbed') : t('variance.held')}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="hv-faint">
                    {t('variance.none')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
