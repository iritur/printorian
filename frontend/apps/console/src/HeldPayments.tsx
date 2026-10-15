import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, formatMoney, translate, translateError } from '@printorian/ui'

import { Field } from './FleetAdmin'

/**
 * «Оплаты на удержании» — money that arrived for an order not waiting for it.
 *
 * Security review §5(d). A customer who pays on a confirmation page the farm had
 * already given up on — after the order was cancelled — used to be charged in
 * silence: the payment read «оплачен», the order stayed «отменён», and no screen
 * listed the pair. The owner's decision is that a person decides, so this panel
 * is where the person is shown it.
 *
 * Two ways out, and neither is a button that guesses. **A refund** goes through
 * the order's own desk below, with the refund controls that already exist, and a
 * fully refunded payment leaves this list by itself. **Keeping it** needs a
 * reason, written here, and `ISSUE_REFUND` — the same narrower permission as
 * sending money back, because it is the same decision made the other way.
 *
 * Rendered only for `VIEW_FINANCIALS` by the page: it is rubles. An empty list is
 * drawn as an empty list rather than hidden, so "nothing is held" is something
 * the screen says rather than something its absence implies.
 */

interface Held {
  payment_id: string
  order_id: string
  provider: string
  status: string
  amount: string
  refunded_amount: string
  currency: string
  settled_at: string | null
  order_status: string
  held_at: string
}

export function HeldPayments({ locale, mayDecide }: { locale: Locale; mayDecide: boolean }) {
  const t = (key: MessageKey) => translate(locale, key)
  const [rows, setRows] = useState<Held[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})

  const describe = useCallback(
    (exc: unknown) =>
      exc instanceof ApiError
        ? translateError(locale, { code: exc.code, details: exc.details })
        : translate(locale, 'error.internal'),
    [locale],
  )

  const load = useCallback(async () => {
    try {
      setRows(await api.get<Held[]>('/payments/held'))
      setError(null)
    } catch (exc: unknown) {
      setError(describe(exc))
    }
  }, [describe])

  useEffect(() => {
    void (async () => {
      await load()
    })()
  }, [load])

  async function release(paymentId: string) {
    try {
      await api.post(`/payments/held/${paymentId}/release`, { note: notes[paymentId] ?? '' })
      await load()
    } catch (exc: unknown) {
      setError(describe(exc))
    }
  }

  // Null is "not loaded yet"; `[]` is "asked, and nothing is held" (ADR-0007).
  if (rows === null) return error ? <p className="hv-faint">{error}</p> : null

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('payments.held.title')}</span>
        <span className="hv-panel__aside">{rows.length}</span>
      </div>
      {error && <p className="hv-hint hv-bad">{error}</p>}
      <div className="hv-panel__body--none">
        <div className="hv-table-wrap">
          <table className="hv-table">
            <thead>
              <tr>
                <th>{t('payments.held.order')}</th>
                <th>{t('payments.held.reason')}</th>
                <th data-align="end">{t('payments.held.amount')}</th>
                <th>{t('payments.held.since')}</th>
                {mayDecide && <th>{t('payments.held.keep')}</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.payment_id}>
                  <td className="hv-mono">{row.order_id.slice(0, 8)}</td>
                  <td>{translate(locale, `order.status.${row.order_status}` as MessageKey)}</td>
                  <td data-align="end">
                    {formatMoney(
                      String(Number(row.amount) - Number(row.refunded_amount)),
                      row.currency,
                      locale,
                    )}
                  </td>
                  <td>{new Date(row.held_at).toLocaleString(locale)}</td>
                  {mayDecide && (
                    <td>
                      <form
                        className="hv-row"
                        onSubmit={(event) => {
                          event.preventDefault()
                          void release(row.payment_id)
                        }}
                      >
                        <Field label={t('payments.held.note')}>
                          <input
                            value={notes[row.payment_id] ?? ''}
                            onChange={(event) =>
                              setNotes({ ...notes, [row.payment_id]: event.target.value })
                            }
                            required
                            maxLength={500}
                          />
                        </Field>
                        <button className="hv-btn hv-btn--sm" type="submit">
                          {t('payments.held.release')}
                        </button>
                      </form>
                    </td>
                  )}
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={mayDecide ? 5 : 4} className="hv-faint">
                    {t('payments.held.none')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      {rows.length > 0 && <p className="hv-hint">{t('payments.held.refund_hint')}</p>}
    </section>
  )
}
