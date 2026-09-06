import { useState } from 'react'

import { api, translate, translateError } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { formatQuantity } from './format'
import type { PurchaseOrderView } from './types'

/**
 * «Приёмка» — counting a delivery in against the order.
 *
 * The near-irreversible act on this screen, so the form is deliberately dull:
 * one row per line, nothing pre-filled, and nothing sent for a line the person
 * standing at the door left blank. Pre-filling the outstanding quantity would
 * make "accept everything" the default gesture, and the whole reason a receipt
 * exists is that what arrived and what was ordered are two different facts.
 *
 * **A lot number is typed against each line and travels with it.** It becomes
 * `material_lots.lot_number` — the thread a recall is pulled by — and it is the
 * one field here nothing else in the system can reconstruct afterwards.
 *
 * **A class this build cannot put into stock is disabled rather than refused
 * after the fact.** `is_receivable` comes off the server, so the console and the
 * API agree about which lines can arrive instead of the console guessing and the
 * request 400ing at the end of a form somebody has already filled in.
 */

interface Entry {
  quantity: string
  unit_price_paid: string
  lot_number: string
  shelf: string
}

const BLANK: Entry = { quantity: '', unit_price_paid: '', lot_number: '', shelf: '' }

export function Receiving({
  order,
  locale,
  mayReceive,
  onChanged,
}: {
  order: PurchaseOrderView
  locale: Locale
  /**
   * `MANAGE_INVENTORY` **and** `VIEW_FINANCIALS`, because the body carries
   * `unit_price_paid`: a request is as much a place money crosses the boundary
   * as a response is.
   */
  mayReceive: boolean
  onChanged: (order: PurchaseOrderView) => void
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)
  const [entries, setEntries] = useState<Record<string, Entry>>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const entryFor = (lineId: string) => entries[lineId] ?? BLANK
  const set = (lineId: string, field: keyof Entry, value: string) =>
    setEntries((current) => {
      const entry: Entry = { ...(current[lineId] ?? BLANK) }
      entry[field] = value
      return { ...current, [lineId]: entry }
    })

  // Only the lines somebody actually typed a quantity against. A body carrying
  // every line with a zero would be a delivery claiming nothing arrived, which
  // the server would rightly refuse and which reads as a bug rather than as an
  // empty form.
  const filled = order.lines.filter((line) => entryFor(line.id).quantity.trim() !== '')

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const body = {
        lines: filled.map((line) => {
          const entry = entryFor(line.id)
          return {
            line_id: line.id,
            quantity: entry.quantity,
            // Empty stays null rather than becoming zero: a delivery counted at
            // the door before the invoice caught up has no price, and free is a
            // different claim.
            unit_price_paid: entry.unit_price_paid.trim() === '' ? null : entry.unit_price_paid,
            lot_number: entry.lot_number.trim() === '' ? null : entry.lot_number,
            shelf: entry.shelf.trim() === '' ? null : entry.shelf,
          }
        }),
      }
      onChanged(
        await api.post<PurchaseOrderView>(`/purchasing/orders/${order.id}/receive`, body),
      )
      setEntries({})
    } catch (exc: unknown) {
      const failure = exc as { code?: string; details?: Record<string, unknown> }
      setError(
        typeof failure?.code === 'string'
          ? translateError(locale, {
              code: failure.code,
              ...(failure.details ? { details: failure.details } : {}),
            })
          : t('error.internal'),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('pu.receive.title')}</span>
        <span className="hv-panel__aside">{t('pu.receive.aside')}</span>
      </div>
      <div className="hv-panel__body hv-stack">
        {/*
          The kit's own footer says the purchase price reaches the tariff after
          receiving. It does not, and this says so where the person is standing:
          a receipt moving `MaterialSpec.purchase_price_per_1000m` would be a
          rate edit with no settings audit and no snapshot behind it, under a
          guarantee (ADR-0020) that a rate change never reprices quoted work.
        */}
        <p className="hv-prose">{t('pu.receive.note')}</p>
        {/*
        `.notice` is deliberately neutral — console.css says so at the rule:
        it carries "this screen is not yours", where nothing has gone wrong.
        A refused delivery has, so the tone comes from Harvester's own
        `hv-bad` rather than from a `notice--` modifier no stylesheet
        defines.
      */}
      {error && <p className="notice hv-bad">{error}</p>}
        <table className="hv-table">
          <thead>
            <tr>
              <th>{t('pu.lines.item')}</th>
              <th data-align="end">{t('pu.receive.outstanding')}</th>
              <th>{t('pu.receive.quantity')}</th>
              <th>{t('pu.receive.lot')}</th>
              <th>{t('pu.receive.price')}</th>
              <th>{t('pu.receive.shelf')}</th>
            </tr>
          </thead>
          <tbody>
            {order.lines.map((line) => {
              const entry = entryFor(line.id)
              const outstanding = Number(line.quantity) - Number(line.received_quantity)
              return (
                <tr key={line.id}>
                  <td>{line.item_name || line.item_code}</td>
                  <td data-align="end">
                    {formatQuantity(String(outstanding), line.unit, locale)}
                  </td>
                  <td>
                    <input
                      className="hv-input"
                      type="text"
                      inputMode="decimal"
                      disabled={!mayReceive || !line.is_receivable}
                      aria-label={t('pu.receive.quantity_for', {
                        item: line.item_name || line.item_code,
                      })}
                      value={entry.quantity}
                      onChange={(event) => set(line.id, 'quantity', event.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="hv-input"
                      type="text"
                      disabled={!mayReceive || !line.is_receivable}
                      aria-label={t('pu.receive.lot_for', {
                        item: line.item_name || line.item_code,
                      })}
                      value={entry.lot_number}
                      onChange={(event) => set(line.id, 'lot_number', event.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="hv-input"
                      type="text"
                      inputMode="decimal"
                      disabled={!mayReceive || !line.is_receivable}
                      aria-label={t('pu.receive.price_for', {
                        item: line.item_name || line.item_code,
                      })}
                      value={entry.unit_price_paid}
                      onChange={(event) => set(line.id, 'unit_price_paid', event.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      className="hv-input"
                      type="text"
                      disabled={!mayReceive || !line.is_receivable}
                      aria-label={t('pu.receive.shelf_for', {
                        item: line.item_name || line.item_code,
                      })}
                      value={entry.shelf}
                      onChange={(event) => set(line.id, 'shelf', event.target.value)}
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="hv-panel__foot">
        <span>{t('pu.receive.foot')}</span>
        <button
          className="hv-btn hv-btn--sm hv-btn--primary"
          type="button"
          disabled={busy || !mayReceive || filled.length === 0}
          onClick={() => void submit()}
        >
          {t('pu.receive.submit')}
        </button>
      </div>
    </section>
  )
}
