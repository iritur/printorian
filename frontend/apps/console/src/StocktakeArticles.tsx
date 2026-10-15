import { useState } from 'react'

import type { Locale, MessageKey } from '@printorian/ui'
import { translate } from '@printorian/ui'

import { amount } from './Articles'

/**
 * The stocktake's article lines — packaging, consumables, spare parts (#35).
 *
 * Apart from the spool lines in `Stocktake.tsx` because they are counted in their
 * own unit: the spool table's columns are grams, and a box count in a grams
 * column is a number with the wrong unit beside it. The rules are the spools'
 * rules — an uncounted line is a dash, never zero, and counting again overwrites
 * while the stocktake is open.
 */

/** `StocktakeArticleLineView`, hand-declared. */
export interface ArticleLine {
  article_id: string
  code: string
  name: string
  unit: string
  cell_address: string | null
  expected: string
  /** Null until counted. Absent, not zero. */
  counted: string | null
  counted_at: string | null
  variance: string | null
}

export function ArticleCountTable({
  lines,
  locale,
  mayManage,
  onCount,
}: {
  lines: ArticleLine[]
  locale: Locale
  mayManage: boolean
  onCount: (articleId: string, quantity: string) => Promise<void>
}) {
  const t = (key: MessageKey) => translate(locale, key)
  if (lines.length === 0) return null
  return (
    <table className="hv-table">
      <thead>
        <tr>
          <th>{t('store.articles.item')}</th>
          <th>{t('store.cell.address')}</th>
          <th data-align="end">{t('store.stocktake.expected')}</th>
          <th data-align="end">{t('store.stocktake.counted')}</th>
          {mayManage && <th />}
        </tr>
      </thead>
      <tbody>
        {lines.map((line) => (
          <ArticleCountRow
            key={line.article_id}
            line={line}
            locale={locale}
            mayManage={mayManage}
            onCount={onCount}
          />
        ))}
      </tbody>
    </table>
  )
}

function ArticleCountRow({
  line,
  locale,
  mayManage,
  onCount,
}: {
  line: ArticleLine
  locale: Locale
  mayManage: boolean
  onCount: (articleId: string, quantity: string) => Promise<void>
}) {
  const [quantity, setQuantity] = useState('')
  const label = `${line.code} ${line.name}`.trim()
  return (
    <tr>
      <td>
        <span className="hv-table__id">{line.code}</span> {line.name}
      </td>
      <td className="hv-table__id">{line.cell_address ?? '—'}</td>
      <td data-align="end">{amount(locale, line.expected, line.unit)}</td>
      {/* Not counted yet: a dash. Zero is a count somebody made. */}
      <td data-align="end">
        {line.counted === null ? '—' : amount(locale, line.counted, line.unit)}
      </td>
      {mayManage && (
        <td>
          <form
            className="hv-row"
            onSubmit={(event) => {
              event.preventDefault()
              void onCount(line.article_id, quantity).then(() => setQuantity(''))
            }}
          >
            <input
              type="number"
              min={0}
              step="any"
              aria-label={label}
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              required
            />
            <button className="hv-btn hv-btn--sm" type="submit">
              ✓
            </button>
          </form>
        </td>
      )}
    </tr>
  )
}
