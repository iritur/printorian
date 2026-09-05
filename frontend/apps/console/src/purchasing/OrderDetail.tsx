import { useCallback, useEffect, useState } from 'react'

import { Modal, api, translate, translateError } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { Receiving } from './Receiving'
import {
  formatDay,
  formatMoney,
  formatQuantity,
  formatStamp,
  kindKey,
  statusKey,
} from './format'
import { PIPE } from './types'
import type {
  PurchaseOrderCost,
  PurchaseOrderView,
  PurchaseStageView,
  PurchaseStatus,
  SupplierView,
} from './types'

/**
 * One purchase order, open: where it is, what is on it, and what has arrived.
 *
 * **The pipe is drawn from the server's own stages**, not from the current
 * status and an assumption about the road behind it. An order that went from
 * paid straight to receiving has no transit time, and the step shows an em dash
 * rather than borrowing its neighbour's timestamp — which is what a client
 * reconstructing the path from `status` alone would necessarily invent.
 *
 * **The money is a second request, not a hidden column.** `/costs` needs
 * `VIEW_FINANCIALS` on top of `MANAGE_INVENTORY`, so a manager without it never
 * asks: there is no panel, no blanked figures and no 403 in the console. Drawing
 * the panel with dashes would spell "not permitted" the way this app spells "not
 * measured", and the two must stay distinguishable (ADR-0007).
 */

export function OrderDetail({
  order,
  suppliers,
  locale,
  maySeeMoney,
  onChanged,
  onClose,
}: {
  order: PurchaseOrderView
  suppliers: SupplierView[]
  locale: Locale
  maySeeMoney: boolean
  onChanged: (order: PurchaseOrderView) => void
  onClose: () => void
}) {
  const t = useCallback(
    (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details),
    [locale],
  )
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [costs, setCosts] = useState<PurchaseOrderCost | null>(null)
  const [supplierId, setSupplierId] = useState('')
  const [newLine, setNewLine] = useState({ item_code: '', quantity: '', unit: 'gram' })

  // Re-read whenever the order changes, because advancing a stage or receiving a
  // line changes what the money panel would say. Guarded on the permission
  // rather than on the response: a caller without `VIEW_FINANCIALS` must not
  // make the request at all, or the console's own network log becomes the place
  // the split leaks.
  useEffect(() => {
    if (!maySeeMoney) return
    let live = true
    void (async () => {
      try {
        const body = await api.get<PurchaseOrderCost>(`/purchasing/orders/${order.id}/costs`)
        if (live) setCosts(body)
      } catch {
        // The order itself is on screen and readable; a costs panel that could
        // not be fetched is a missing panel, not a broken order.
        if (live) setCosts(null)
      }
    })()
    return () => {
      live = false
    }
  }, [order.id, order.status, order.lines.length, order.receipts.length, maySeeMoney])

  const run = async (work: () => Promise<PurchaseOrderView>) => {
    setBusy(true)
    setError(null)
    try {
      onChanged(await work())
    } catch (exc: unknown) {
      setError(describe(exc, locale))
    } finally {
      setBusy(false)
    }
  }

  const advance = (to: string) =>
    run(() => api.post<PurchaseOrderView>(`/purchasing/orders/${order.id}/status`, { to }))

  const next = NEXT_STAGE[order.status]

  return (
    <Modal
      wide
      title={`${t('pu.detail.title')} :: ${order.number}`}
      meta={[
        { label: t('pu.detail.supplier'), value: order.supplier?.name ?? t('pu.supplier.none') },
        { label: t('pu.detail.expected'), value: formatDay(order.expected_at, locale) },
        { label: t('pu.detail.lines'), value: String(order.lines.length) },
      ]}
      status={t(statusKey(order.status))}
      path={`/SUPPLY/PURCHASING/${order.number}`}
      onClose={onClose}
      footer={
        <>
          <span>{order.number} · SUPPLY.PURCHASING</span>
          <button className="hv-btn hv-btn--sm" type="button" onClick={onClose}>
            {t('common.close')}
          </button>
        </>
      }
    >
      {error && <p className="notice notice--bad">{error}</p>}

      {/* --------------------------------------------------- «Путь заказа» */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('pu.pipe.title')}</span>
          <span className="hv-panel__aside">{t('pu.pipe.aside', { total: PIPE.length })}</span>
        </div>
        <div className="hv-pipe">
          {order.stages.map((stage, index) => (
            <Stage key={stage.status} stage={stage} index={index} locale={locale} />
          ))}
        </div>
        <div className="hv-panel__foot">
          <span>{t('pu.pipe.foot')}</span>
          {next && (
            <button
              className="hv-btn hv-btn--sm hv-btn--primary"
              type="button"
              disabled={busy}
              onClick={() => void advance(next)}
            >
              {t('pu.action.advance', { stage: t(statusKey(next)) })}
            </button>
          )}
          {next && (
            <button
              className="hv-btn hv-btn--sm hv-btn--danger"
              type="button"
              disabled={busy}
              onClick={() =>
                void run(() =>
                  api.post<PurchaseOrderView>(`/purchasing/orders/${order.id}/cancel`, {}),
                )
              }
            >
              {t('pu.action.cancel')}
            </button>
          )}
        </div>
      </section>

      <div className="hv-cols hv-cols--2">
        {/* ------------------------------------------------ «Состав заказа» */}
        <section className="hv-panel">
          <div className="hv-panel__head">
            <span>{t('pu.lines.title')}</span>
            <span className="hv-panel__aside">{t('pu.lines.aside', { count: order.lines.length })}</span>
          </div>
          <div className="hv-panel__body--none">
            <table className="hv-table">
              <thead>
                <tr>
                  <th>{t('pu.lines.item')}</th>
                  <th>{t('pu.lines.kind')}</th>
                  <th data-align="end">{t('pu.lines.ordered')}</th>
                  <th data-align="end">{t('pu.lines.received')}</th>
                </tr>
              </thead>
              <tbody>
                {order.lines.map((line) => (
                  <tr key={line.id}>
                    <td>{line.item_name || line.item_code}</td>
                    <td>{t(kindKey(line.kind))}</td>
                    <td data-align="end">{formatQuantity(line.quantity, line.unit, locale)}</td>
                    <td data-align="end">
                      {formatQuantity(line.received_quantity, line.unit, locale)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/*
            Lines can only be added while the order is a draft, and the form
            disappears with the stage rather than being disabled: once somebody
            has approved a specific list, quietly extending it would make the
            approval a record of something that never happened. The server
            refuses it too — this is the shape of that rule, not the whole of it.
          */}
          {order.status === 'draft' && (
            <div className="hv-panel__foot">
              <input
                className="hv-input"
                type="text"
                aria-label={t('pu.lines.add_code')}
                placeholder={t('pu.lines.add_code')}
                value={newLine.item_code}
                onChange={(event) =>
                  setNewLine((current) => ({ ...current, item_code: event.target.value }))
                }
              />
              <input
                className="hv-input"
                type="text"
                inputMode="decimal"
                aria-label={t('pu.lines.add_quantity')}
                placeholder={t('pu.lines.add_quantity')}
                value={newLine.quantity}
                onChange={(event) =>
                  setNewLine((current) => ({ ...current, quantity: event.target.value }))
                }
              />
              <button
                className="hv-btn hv-btn--sm"
                type="button"
                disabled={busy || newLine.item_code.trim() === '' || newLine.quantity.trim() === ''}
                onClick={() =>
                  void run(async () => {
                    const updated = await api.post<PurchaseOrderView>(
                      `/purchasing/orders/${order.id}/lines`,
                      [
                        {
                          kind: 'material',
                          item_code: newLine.item_code,
                          quantity: newLine.quantity,
                          unit: newLine.unit,
                        },
                      ],
                    )
                    setNewLine({ item_code: '', quantity: '', unit: 'gram' })
                    return updated
                  })
                }
              >
                {t('pu.lines.add')}
              </button>
            </div>
          )}
        </section>

        <div className="hv-stack">
          {/* ------------------------------------------------- «Поставщик» */}
          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('pu.supplier.title')}</span>
              {/*
                Counts only in this slice. «Поставок · В срок · Брак · Оценка»
                needs a defect record captured at receiving and a promised-versus
                -delivered measure, and on a farm with no receipts every one of
                those columns would be a fabricated denominator.
              */}
              <span className="hv-panel__aside">{t('pu.supplier.aside')}</span>
            </div>
            <div className="hv-panel__body hv-panel__body--tight">
              <p className="hv-prose">{order.supplier?.name ?? t('pu.supplier.none')}</p>
              <div className="hv-row">
                <select
                  className="hv-input"
                  aria-label={t('pu.supplier.choose')}
                  value={supplierId}
                  onChange={(event) => setSupplierId(event.target.value)}
                >
                  <option value="">{t('pu.supplier.choose')}</option>
                  {suppliers.map((supplier) => (
                    <option key={supplier.id} value={supplier.id}>
                      {supplier.name}
                    </option>
                  ))}
                </select>
                <button
                  className="hv-btn hv-btn--sm"
                  type="button"
                  disabled={busy || supplierId === ''}
                  onClick={() =>
                    void run(() =>
                      api.post<PurchaseOrderView>(`/purchasing/orders/${order.id}/supplier`, {
                        supplier_id: supplierId,
                      }),
                    )
                  }
                >
                  {t('pu.supplier.assign')}
                </button>
              </div>
            </div>
          </section>

          {maySeeMoney && <Costs costs={costs} locale={locale} />}
        </div>
      </div>

      <Receiving order={order} locale={locale} mayReceive={maySeeMoney} onChanged={onChanged} />
    </Modal>
  )
}

/** One step of the pipe. `at` is null for a stage not reached — and for one skipped. */
function Stage({
  stage,
  index,
  locale,
}: {
  stage: PurchaseStageView
  index: number
  locale: Locale
}) {
  const state = stage.is_current ? 'now' : stage.at === null ? undefined : 'done'
  return (
    <div className="hv-pipe__step" {...(state ? { 'data-state': state } : {})}>
      <div className="hv-pipe__n">{String(index + 1).padStart(2, '0')}</div>
      <div className="hv-pipe__k">{translate(locale, statusKey(stage.status))}</div>
      <div className="hv-pipe__t">{formatStamp(stage.at, locale)}</div>
    </div>
  )
}

/**
 * «Стоимость заказа» — the only rubles on this screen, and only behind the
 * permission.
 *
 * The total is the server's, including its refusal to compute one: a subtotal of
 * the priced lines presented as "the order total" is smaller than the real
 * figure and reads as authoritative, so an unpriced line makes the total an em
 * dash and `unpriced_lines` says why.
 */
function Costs({ costs, locale }: { costs: PurchaseOrderCost | null; locale: Locale }) {
  const t = (key: MessageKey, details?: Record<string, unknown>) =>
    translate(locale, key, details)
  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('pu.costs.title')}</span>
        <span className="hv-panel__aside">{t('pu.costs.aside')}</span>
      </div>
      <div className="hv-panel__body hv-panel__body--tight">
        {costs === null ? (
          <p className="hv-hint">{t('common.loading')}</p>
        ) : (
          <ul className="hv-leaders">
            {costs.lines.map((line) => (
              <li className="hv-leader" key={line.line_id}>
                <span className="hv-leader__k">{line.item_code}</span>
                <span className="hv-leader__fill" />
                <span className="hv-leader__v">{formatMoney(line.total, locale)}</span>
              </li>
            ))}
            <li className="hv-leader">
              <span className="hv-leader__k">{t('pu.costs.frozen')}</span>
              <span className="hv-leader__fill" />
              <span className="hv-leader__v">{formatMoney(costs.frozen_in_stock, locale)}</span>
            </li>
            <li className="hv-leader">
              <span className="hv-leader__k">{t('pu.costs.total')}</span>
              <span className="hv-leader__fill" />
              <span className="hv-leader__v">{formatMoney(costs.total, locale)}</span>
            </li>
          </ul>
        )}
      </div>
      {costs !== null && costs.unpriced_lines > 0 && (
        <div className="hv-panel__foot">
          <span>{t('pu.costs.unpriced', { count: costs.unpriced_lines })}</span>
        </div>
      )}
    </section>
  )
}

/**
 * The one forward move offered from each stage, mirroring the server's own
 * `TRANSITIONS`.
 *
 * Only the forward one: cancellation is a separate button because it is a
 * separate decision, and `paid` offers transit rather than receiving because a
 * courier arriving early is recorded by the receiving form, not by a stage
 * button somebody pressed on their behalf. A stage this build has never heard of
 * has no entry and offers nothing, which is the honest answer.
 */
const NEXT_STAGE: Partial<Record<PurchaseStatus, PurchaseStatus>> = {
  draft: 'approved',
  approved: 'paid',
  paid: 'in_transit',
  in_transit: 'receiving',
  receiving: 'stored',
}

function describe(exc: unknown, locale: Locale): string {
  const body = exc as { code?: string; details?: Record<string, unknown> }
  if (typeof body?.code !== 'string') return translate(locale, 'error.internal')
  return translateError(locale, {
    code: body.code,
    ...(body.details ? { details: body.details } : {}),
  })
}
