import type { Locale, MessageKey } from '@printorian/ui'
import { translate } from '@printorian/ui'

import type { ArticleMovement } from './Articles'
import { amount } from './Articles'
import type { Movement } from './CellDetail'

/**
 * «Движения» — spools and everything else on one feed, newest first.
 *
 * Two ledgers on the server (`/store/movements` in grams, `/store/articles/
 * movements` in each article's own unit), interleaved here by time. They are kept
 * apart there because a single row shape would have to pretend one unit was the
 * other; here each row says its quantity in its own unit, which is all the kit's
 * «Кол-во» column ever asked for («1 кг», «6 шт», «5 л»).
 *
 * Split out of `StorePage` at the seam when the second ledger arrived (#35).
 */

interface Row {
  id: string
  at: string
  reason: string
  item: string
  route: string
  quantity: string
  basis: string
}

function fromSpool(row: Movement, locale: Locale): Row {
  return {
    id: `lot-${row.id}`,
    at: row.at,
    reason: row.reason,
    item: '',
    route: [row.from_address, row.to_address].map((part) => part ?? '—').join(' → '),
    quantity: amount(locale, row.grams, 'gram'),
    basis: row.note ?? '—',
  }
}

function fromArticle(row: ArticleMovement, locale: Locale): Row {
  return {
    id: `article-${row.id}`,
    at: row.at,
    reason: row.reason,
    item: `${row.code} ${row.name}`.trim(),
    route: [row.from_address, row.to_address].map((part) => part ?? '—').join(' → '),
    quantity: amount(locale, row.quantity, row.unit),
    basis: row.reference ?? row.note ?? '—',
  }
}

export function MovementFeed({
  spools,
  articles,
  locale,
}: {
  spools: Movement[]
  articles: ArticleMovement[]
  locale: Locale
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const rows = [
    ...spools.map((row) => fromSpool(row, locale)),
    ...articles.map((row) => fromArticle(row, locale)),
  ].sort((a, b) => b.at.localeCompare(a.at))

  if (rows.length === 0) return <p className="hv-hint">{t('store.movements.empty')}</p>
  return (
    <table className="hv-table">
      <thead>
        <tr>
          <th>{t('store.movement.time')}</th>
          <th>{t('store.movement.reason')}</th>
          <th>{t('store.articles.item')}</th>
          <th>{t('store.movement.route')}</th>
          <th data-align="end">{t('store.articles.quantity')}</th>
          <th>{t('store.movement.note')}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td className="hv-table__id">{new Date(row.at).toLocaleString(locale)}</td>
            <td>{translate(locale, `store.reason.${row.reason}` as MessageKey)}</td>
            <td>{row.item || '—'}</td>
            <td className="hv-table__id">{row.route}</td>
            <td data-align="end">{row.quantity}</td>
            <td>{row.basis}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
