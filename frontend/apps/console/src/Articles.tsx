import { useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, translate, translateError } from '@printorian/ui'

import { Field } from './FleetAdmin'

/**
 * The non-filament stock — packaging, post-processing consumables, spare parts (#35).
 *
 * Its own file at the seam rather than more rows in `CellDetail` and `StorePage`,
 * both near the console's four-hundred-line convention: a box of film is a
 * different thing on the same shelf, with its own unit, and none of a spool's
 * drying or AMS concerns.
 *
 * **No money here, on purpose.** An article's purchase cost stays on the server
 * and reaches a screen only through the dead-stock report, behind
 * `view_financials`; the store is read by the floor.
 */

/** `ArticleView`, hand-declared — the console's convention (frontend/CLAUDE.md). */
export interface Article {
  id: string
  kind: 'packaging' | 'post_consumable' | 'spare_part'
  code: string
  name: string
  unit: string
  on_hand: string
  reorder_at: string
  /** Null until somebody puts it somewhere — «не размещена», not an empty cell. */
  cell: string | null
  is_active: boolean
}

/** `ArticleMovementView`. */
export interface ArticleMovement {
  id: string
  article_id: string
  kind: Article['kind']
  code: string
  name: string
  unit: string
  reason: string
  quantity: string
  on_hand_after: string
  at: string
  from_address: string | null
  to_address: string | null
  /** The purchase order, parcel or ticket it was for — the kit's «Основание». */
  reference: string | null
  note: string | null
}

const KNOWN_UNITS = new Set(['piece', 'roll', 'kg', 'sheet', 'can', 'pair', 'litre', 'gram'])

/**
 * A unit code in words. The codes are free text written on purchase orders, so an
 * unknown one is shown as written rather than dropped — a number with no unit
 * beside it is how a stock figure stops meaning anything.
 */
export function unitText(locale: Locale, unit: string): string {
  return KNOWN_UNITS.has(unit) ? translate(locale, `store.unit.${unit}` as MessageKey) : unit
}

/** A quantity in its unit: whole numbers without the «.00», fractions as given. */
export function amount(locale: Locale, quantity: string, unit: string): string {
  const value = Number(quantity)
  const shown = Number.isInteger(value) ? String(value) : value.toFixed(2)
  return `${shown} ${unitText(locale, unit)}`
}

/** Every stocked article, by kind, with the unplaced ones saying so. */
export function ArticlesPanel({
  articles,
  locale,
  mayManage,
  onChanged,
}: {
  articles: Article[]
  locale: Locale
  mayManage: boolean
  onChanged: () => Promise<void>
}) {
  const t = (key: MessageKey) => translate(locale, key)
  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('store.articles.title')}</span>
        <span className="hv-panel__aside">{articles.length}</span>
      </div>
      <div className="hv-panel__body--none">
        {articles.length === 0 ? (
          <p className="hv-hint">{t('store.articles.empty')}</p>
        ) : (
          <ArticleTable
            rows={articles}
            locale={locale}
            mayManage={mayManage}
            onDone={onChanged}
            withCell
          />
        )}
      </div>
    </section>
  )
}

/** The articles in one cell, for its window. */
export function CellArticles({
  articles,
  locale,
  mayManage,
  onDone,
}: {
  articles: Article[]
  locale: Locale
  mayManage: boolean
  onDone: () => Promise<void>
}) {
  const t = (key: MessageKey) => translate(locale, key)
  if (articles.length === 0) return null
  return (
    <section>
      <div className="hv-panel__head">
        <span>{t('store.articles.in_cell')}</span>
      </div>
      <ArticleTable rows={articles} locale={locale} mayManage={mayManage} onDone={onDone} />
    </section>
  )
}

function ArticleTable({
  rows,
  locale,
  mayManage,
  onDone,
  withCell = false,
}: {
  rows: Article[]
  locale: Locale
  mayManage: boolean
  onDone: () => Promise<void>
  withCell?: boolean
}) {
  const t = (key: MessageKey) => translate(locale, key)
  return (
    <table className="hv-table">
      <thead>
        <tr>
          <th>{t('store.articles.kind')}</th>
          <th>{t('store.articles.item')}</th>
          {withCell && <th>{t('store.articles.cell')}</th>}
          <th data-align="end">{t('store.articles.on_hand')}</th>
          {mayManage && <th>{t('store.movement.reason')}</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const low = Number(row.reorder_at) > 0 && Number(row.on_hand) <= Number(row.reorder_at)
          return (
            <tr key={row.id}>
              <td>{translate(locale, `store.kind.${row.kind}` as MessageKey)}</td>
              <td>
                <span className="hv-table__id">{row.code}</span> {row.name}
              </td>
              {withCell && (
                <td className={row.cell ? 'hv-table__id' : 'hv-warn'}>
                  {row.cell ?? t('store.articles.unplaced')}
                </td>
              )}
              <td data-align="end" data-sentiment={low ? 'bad' : undefined}>
                {amount(locale, row.on_hand, row.unit)}
              </td>
              {mayManage && (
                <td>
                  <ArticleActions article={row} locale={locale} onDone={onDone} />
                </td>
              )}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/**
 * Put it somewhere, hand some to the floor, or write some off.
 *
 * Issue and write-off share one quantity field and differ in the button: both take
 * stock off the shelf, and the reason column in the ledger is what tells them
 * apart afterwards. Both are refused by the server above what the book holds.
 */
function ArticleActions({
  article,
  locale,
  onDone,
}: {
  article: Article
  locale: Locale
  onDone: () => Promise<void>
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const [target, setTarget] = useState('')
  const [quantity, setQuantity] = useState('')
  const [reference, setReference] = useState('')
  const [failed, setFailed] = useState<string | null>(null)

  const send = async (run: () => Promise<unknown>) => {
    try {
      await run()
      setFailed(null)
      setQuantity('')
      setReference('')
      await onDone()
    } catch (exc: unknown) {
      setFailed(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
    }
  }
  const take = (path: 'issue' | 'write-off') =>
    send(() =>
      api.post(`/store/articles/${article.id}/${path}`, {
        quantity,
        reference: reference || null,
      }),
    )

  return (
    <div className="hv-stack">
      {failed && <p className="hv-hint hv-bad">{failed}</p>}
      <form
        className="hv-row"
        onSubmit={(event) => {
          event.preventDefault()
          void send(async () => {
            await api.post(`/store/articles/${article.id}/place`, { address: target })
            setTarget('')
          })
        }}
      >
        <Field label={t('store.cell.address')}>
          <input value={target} onChange={(event) => setTarget(event.target.value)} required />
        </Field>
        <button className="hv-btn hv-btn--sm" type="submit">
          {t('store.reason.stock.moved')}
        </button>
      </form>
      <div className="hv-row">
        <Field label={t('store.articles.quantity')}>
          <input
            inputMode="decimal"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
          />
        </Field>
        <Field label={t('store.articles.reference')}>
          <input
            value={reference}
            maxLength={80}
            onChange={(event) => setReference(event.target.value)}
          />
        </Field>
        <button
          className="hv-btn hv-btn--sm"
          type="button"
          disabled={!quantity}
          onClick={() => void take('issue')}
        >
          {t('store.reason.stock.issued')}
        </button>
        <button
          className="hv-btn hv-btn--sm"
          type="button"
          disabled={!quantity}
          onClick={() => void take('write-off')}
        >
          {t('store.reason.stock.written_off')}
        </button>
      </div>
    </div>
  )
}
