import { useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, formatMoney, translate, translateError } from '@printorian/ui'

/**
 * The management actions on one order: move it along, and give money back.
 *
 * ## The transition list comes from the server
 *
 * `allowed_transitions` is computed from the ordering context's `TRANSITIONS`
 * table and sent with the order. Keeping a copy of that table here would put the
 * same state machine in two languages, and the first time they disagreed this
 * screen would offer a button the API refuses. Same reasoning as ADR-0015.
 */

export const MANAGE_ORDER = 'manage_order'
export const ISSUE_REFUND = 'issue_refund'

export interface DeskOrder {
  id: string
  number: string
  status: string
  currency: string
  total: string
  sla_credit: string
  allowed_transitions: string[]
}

interface Payment {
  id: string
  status: string
  amount: string
  currency: string
  provider: string
  refunded_amount?: string
}

function describe(exc: unknown, locale: Locale): string {
  return exc instanceof ApiError
    ? translateError(locale, { code: exc.code, details: exc.details })
    : translate(locale, 'error.internal')
}

export function OrderDesk({
  order,
  locale,
  mayAdvance,
  mayRefund,
  onChanged,
}: {
  order: DeskOrder
  locale: Locale
  mayAdvance: boolean
  mayRefund: boolean
  onChanged: () => void
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [target, setTarget] = useState('')
  const [reason, setReason] = useState('')
  const [payments, setPayments] = useState<Payment[] | null>(null)

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await work()
      onChanged()
    } catch (exc: unknown) {
      setError(describe(exc, locale))
    } finally {
      setBusy(false)
    }
  }

  const loadPayments = async () => {
    try {
      setPayments(await api.get<Payment[]>(`/payments/order/${order.id}`))
    } catch (exc: unknown) {
      setError(describe(exc, locale))
    }
  }

  if (!mayAdvance && !mayRefund) return null

  return (
    <div className="desk">
      {error && <p className="cfg__error">{error}</p>}

      {mayAdvance && (
        <form
          className="admin-detail__row"
          onSubmit={(event) => {
            event.preventDefault()
            void run(() =>
              api.post(`/orders/${order.id}/advance`, { target, reason }),
            ).then(() => {
              setTarget('')
              setReason('')
            })
          }}
        >
          <label className="cfg__field">
            <span>{t('desk.advance')}</span>
            {order.allowed_transitions.length === 0 ? (
              <em className="admin-detail__muted">{t('desk.advance.none')}</em>
            ) : (
              <select
                required
                value={target}
                onChange={(event) => setTarget(event.target.value)}
              >
                <option value="">—</option>
                {order.allowed_transitions.map((status) => (
                  <option key={status} value={status}>
                    {translate(locale, `order.status.${status}` as MessageKey)}
                  </option>
                ))}
              </select>
            )}
          </label>

          {order.allowed_transitions.length > 0 && (
            <>
              <label className="cfg__field">
                <span>{t('desk.advance.reason')}</span>
                <input value={reason} onChange={(event) => setReason(event.target.value)} />
              </label>
              <button type="submit" disabled={busy || !target}>
                {t('common.save')}
              </button>
            </>
          )}
        </form>
      )}

      {mayRefund && (
        <section className="desk__payments">
          <h4>{t('desk.payments')}</h4>
          {payments === null ? (
            <button type="button" onClick={() => void loadPayments()}>
              {t('desk.payments')}
            </button>
          ) : payments.length === 0 ? (
            <p className="admin-detail__muted">{t('desk.no_payments')}</p>
          ) : (
            <ul className="admin-detail__list">
              {payments.map((payment) => (
                <li key={payment.id}>
                  <span>
                    {formatMoney(payment.amount, payment.currency, locale)} ·{' '}
                    {payment.provider} · {payment.status}
                  </span>
                  <RefundControls
                    payment={payment}
                    order={order}
                    locale={locale}
                    busy={busy}
                    onRun={run}
                  />
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}

function RefundControls({
  payment,
  order,
  locale,
  busy,
  onRun,
}: {
  payment: Payment
  order: DeskOrder
  locale: Locale
  busy: boolean
  onRun: (work: () => Promise<unknown>) => Promise<void>
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const [open, setOpen] = useState(false)
  const [amount, setAmount] = useState(payment.amount)
  const [reason, setReason] = useState('')

  const owesCredit = Number(order.sla_credit) > 0

  if (!open) {
    return (
      <span className="desk__refund-actions">
        <button type="button" disabled={busy} onClick={() => setOpen(true)}>
          {t('desk.refund')}
        </button>
        {/* A dedicated call, because "pay back exactly what lateness owes" is a
            different decision from "refund some amount someone typed". */}
        {owesCredit && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void onRun(() => api.post(`/payments/${payment.id}/refund-sla-credit`))
            }
          >
            {t('desk.refund.sla')}
          </button>
        )}
      </span>
    )
  }

  return (
    <form
      className="admin-detail__row"
      onSubmit={(event) => {
        event.preventDefault()
        void onRun(() =>
          api.post(`/payments/${payment.id}/refund`, { amount, reason }),
        ).then(() => setOpen(false))
      }}
    >
      <label className="cfg__field">
        <span>{t('desk.refund.amount')}</span>
        <input
          type="number"
          min="0"
          step="0.01"
          max={payment.amount}
          required
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
        />
      </label>
      <label className="cfg__field">
        <span>{t('desk.refund.reason')}</span>
        <input required value={reason} onChange={(event) => setReason(event.target.value)} />
      </label>
      <button type="submit" disabled={busy}>
        {t('desk.refund.confirm')}
      </button>
      <button type="button" onClick={() => setOpen(false)} disabled={busy}>
        {t('common.cancel')}
      </button>
    </form>
  )
}
