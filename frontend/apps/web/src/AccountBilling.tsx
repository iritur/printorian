import { useEffect, useState } from 'react'

import type { Locale } from '@printorian/ui'
import { api } from '@printorian/ui'

import { NONE, formatMoney, shortDate } from './account'
import type { Receipt } from './account'

/**
 * «Оплата и документы».
 *
 * **There are no saved cards, and this panel says so rather than drawing an
 * empty list under a heading that promises them.** Saving a card means holding a
 * gateway token, which means a gateway: this farm settles through the `mock` and
 * `manual` providers, and the YooKassa adapter has never been run against the
 * real thing (see the README). Building the entity and the endpoints for tokens
 * nothing can issue would be plumbing that can never be tested — and a panel
 * showing «карт нет» would read as "you have not added one yet" when the truth
 * is "the farm cannot store one".
 *
 * The documents half is entirely real. A receipt *is* a settled payment and a
 * refund note *is* a succeeded refund, derived at read time from the payments
 * this customer's orders actually have — there is no documents table, because a
 * second record of money is a second thing that can disagree with it.
 */

/** What the row is. Codes cross the wire (ADR-0012); the names live here. */
const KIND_LABEL: Record<string, string> = { receipt: 'Чек', refund: 'Возврат' }

/**
 * The two the checkout offers, and the same two `CheckoutForm.PaymentStep`
 * draws.
 *
 * Not fetched. `/payments/providers/available` answers with which gateway the
 * deployment is configured for, and it is gated on `VIEW_FINANCIALS` — staff
 * only, and rightly: which acquirer the farm settles through is the farm's
 * business, not a fact to publish on a customer's account screen. What the
 * customer chooses between is these two, which never change per deployment.
 */
const METHODS: [string, string][] = [
  ['Онлайн-оплата', 'Карта или платёжный сервис на стороне провайдера'],
  ['Счёт', 'Реквизиты для перевода. Для юридических лиц'],
]

export function AccountBilling({ locale }: { locale: Locale }) {
  const [rows, setRows] = useState<Receipt[] | null>(null)

  useEffect(() => {
    void api
      .get<Receipt[]>('/account/documents')
      .then(setRows)
      .catch(() => setRows([]))
  }, [])

  const total = (rows ?? [])
    .filter((row) => row.kind === 'receipt')
    .reduce((sum, row) => sum + Number(row.amount), 0)
  const currency = rows?.[0]?.currency ?? 'RUB'

  return (
    <>
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>Способы оплаты</span>
          <span className="hv-panel__aside">НИЧЕГО НЕ СОХРАНЕНО</span>
        </div>
        <div className="hv-panel__body hv-stack hv-stack--2">
          {METHODS.map(([name, note]) => (
            <div key={name} className="hv-record">
              <span className="hv-brand-mark" />
              <span>
                <span className="hv-record__k">{name}</span>
                <span className="hv-record__v">{note}</span>
              </span>
              <span className="hv-record__badge">При оформлении</span>
            </div>
          ))}
          <p className="hv-micro" style={{ margin: 'var(--hv-2) 0 0' }}>
            КАРТА НЕ СОХРАНЯЕТСЯ. PRINTORIAN НЕ ХРАНИТ НИ НОМЕРА, НИ ТОКЕНА — РЕКВИЗИТЫ
            ВВОДЯТСЯ НА СТОРОНЕ ПЛАТЁЖНОГО ПРОВАЙДЕРА ПРИ КАЖДОЙ ОПЛАТЕ.
          </p>
        </div>
      </section>

      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>Документы</span>
          <span className="hv-panel__aside">{rows === null ? NONE : rows.length}</span>
        </div>
        <div className="hv-panel__body--none">
          <div className="hv-table-wrap">
            <table className="hv-table">
              <thead>
                <tr>
                  <th>Документ</th>
                  <th>Заказ</th>
                  <th>Дата</th>
                  <th data-align="end">Сумма</th>
                </tr>
              </thead>
              <tbody>
                {(rows ?? []).map((row) => (
                  <tr key={`${row.kind}-${row.order_id}-${row.issued_at}`}>
                    <td>{KIND_LABEL[row.kind] ?? row.kind}</td>
                    <td className="hv-table__id">{row.order_number}</td>
                    <td>{shortDate(row.issued_at, locale)}</td>
                    <td data-align="end" className={row.kind === 'refund' ? 'hv-good' : undefined}>
                      {formatMoney(row.amount, row.currency, locale)}
                    </td>
                  </tr>
                ))}
                {rows !== null && rows.length === 0 && (
                  <tr>
                    <td colSpan={4} className="hv-faint">
                      Оплаченных заказов пока нет — документы появятся после первой оплаты.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
        <div className="hv-panel__foot">
          <span>
            ЗА ВСЁ ВРЕМЯ :: {rows && rows.length > 0 ? formatMoney(String(total), currency, locale) : NONE}
          </span>
          {/*
            No «Выгрузить все» button. The kit has one and there is nothing
            behind it: the farm generates no PDFs, so the control would open
            nothing. The CSV of the order history — which is real — is on the
            «Заказы» section where it belongs.
          */}
          <span>ЧЕК ФОРМИРУЕТ ПЛАТЁЖНЫЙ ПРОВАЙДЕР</span>
        </div>
      </section>
    </>
  )
}
