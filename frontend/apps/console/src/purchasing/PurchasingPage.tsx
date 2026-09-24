import { useCallback, useEffect, useMemo, useState } from 'react'

import { FilterChips, api, translate, useChrome, useSession } from '@printorian/ui'
import type { FilterChip, Locale, MessageKey } from '@printorian/ui'

import { OrderDetail } from './OrderDetail'
import { PriceMovements } from './PriceMovements'
import {
  NOT_MEASURED,
  formatDay,
  formatQuantity,
  kindKey,
  statusKey,
  statusTone,
} from './format'
import { ALL_STATUSES } from './types'
import type {
  PurchasableKind,
  PurchaseOrderView,
  PurchaseStatus,
  PurchasePrices,
  PurchasingBoard,
  ReorderRow,
  SupplierScore,
  SupplierView,
} from './types'

/**
 * The purchasing desk: what the farm is short of, and what it has on order.
 *
 * **«Последствие» is an em dash for filament, and that is the point of the
 * screen rather than a gap in it.** `design/purchasing.html` puts «1.2 месяца»
 * in that column. Nothing in this system measures material consumption —
 * `material_lots.remaining_grams` is written once, at lot creation, and never
 * decremented — so a months figure could only be derived from lot count or from
 * the roster, and it would look most reassuring exactly when coverage is worst.
 * The server sends a discriminated consequence whose coverage arm does not
 * exist, and this draws what it actually sent.
 *
 * **No money anywhere on this page.** `/purchasing/board` carries none, and the
 * order total column the kit draws is gone with it: a manager without
 * `VIEW_FINANCIALS` is refused `/costs` outright rather than served the same
 * screen with the numbers blanked, because a blank already means "not measured".
 * The detail popup asks for prices only when the actor may read them.
 *
 * Refetched rather than patched after every write, for the reason the other
 * boards are: raising an order moves a chip, a row and possibly the reorder
 * list, and a client trying to patch all three would be reimplementing the read
 * model in TypeScript.
 */

const MANAGE_INVENTORY = 'manage_inventory'
const VIEW_FINANCIALS = 'view_financials'

export function PurchasingPage({ locale }: { locale: Locale }) {
  const { actor, ready } = useSession()
  const t = useCallback(
    (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details),
    [locale],
  )

  const [board, setBoard] = useState<PurchasingBoard | null>(null)
  const [suppliers, setSuppliers] = useState<SupplierView[]>([])
  const [prices, setPrices] = useState<PurchasePrices | null>(null)
  const [open, setOpen] = useState<PurchaseOrderView | null>(null)
  const [status, setStatus] = useState<PurchaseStatus | null>(null)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)

  const entitled = actor?.permissions.includes(MANAGE_INVENTORY) ?? false
  const maySeeMoney = actor?.permissions.includes(VIEW_FINANCIALS) ?? false

  const refetch = useCallback(async () => {
    try {
      setBoard(await api.get<PurchasingBoard>('/purchasing/board'))
    } catch (exc: unknown) {
      // Keep the last board rather than blanking a screen somebody is working
      // from; a stale list is a smaller lie than an empty one.
      console.warn('purchasing refresh failed', exc)
    } finally {
      setLoading(false)
    }
    // The prices are a second request behind `VIEW_FINANCIALS`, refetched with
    // the board because a delivery received from the detail popup is a new
    // point on the panel. Never asked for without the permission: the route
    // would refuse, and a refused request in the network log is still a request
    // for money by somebody who may not see it.
    if (!maySeeMoney) return
    try {
      setPrices(await api.get<PurchasePrices>('/purchasing/prices'))
    } catch (exc: unknown) {
      console.warn('purchasing prices refresh failed', exc)
    }
  }, [maySeeMoney])

  useEffect(() => {
    if (!ready || !entitled) return
    // Awaited inside the effect rather than called from its body, which is what
    // `PackagingPage` does and for the same reason: `react-hooks/set-state-in-effect`
    // reads a plain `refetch()` here as a synchronous setState and a cascading
    // render. Two separate closures rather than one, so the supplier list is not
    // queued behind the board — the «Новый заказ» form needs it and the board is
    // the slower of the two.
    void (async () => {
      await refetch()
    })()
    void (async () => {
      try {
        setSuppliers(await api.get<SupplierView[]>('/purchasing/suppliers'))
      } catch {
        setSuppliers([])
      }
    })()
  }, [ready, entitled, refetch])

  useChrome(
    board === null
      ? null
      : {
          path: '/SUPPLY/PURCHASING/ORDERS',
          meta: [
            { label: 'ORDERS', value: String(board.total) },
            { label: 'REORDER', value: String(board.reorder.length) },
          ],
        },
  )

  /**
   * The chips, one per stage the server counted — including the empty ones.
   *
   * A chip reading «Отменён 0» is information; a missing chip is a gap a person
   * has to notice. Counts come off the response rather than from counting the
   * rows on screen, so a chip cannot disagree with the filter it applies.
   */
  const chips = useMemo<FilterChip[]>(
    () =>
      ALL_STATUSES.map((value) => {
        const found = board?.counts.find((count) => count.status === value)
        return {
          key: value,
          label: translate(locale, statusKey(value)),
          // `null`, not `0`, before the board has loaded: nothing has been
          // counted yet, and «—» says so.
          count: found ? found.count : null,
          tone: statusTone(value),
        }
      }),
    [board, locale],
  )

  const orders = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return (board?.orders ?? []).filter((order) => {
      if (status !== null && order.status !== status) return false
      if (needle === '') return true
      return (
        order.number.toLowerCase().includes(needle) ||
        (order.supplier_name ?? '').toLowerCase().includes(needle)
      )
    })
  }, [board, status, query])

  const openOrder = async (id: string) => {
    setOpen(await api.get<PurchaseOrderView>(`/purchasing/orders/${id}`))
  }

  const raise = async (seed: boolean) => {
    const created = await api.post<PurchaseOrderView>('/purchasing/orders', {
      seed_from_reorder: seed,
      lines: [],
    })
    await refetch()
    setOpen(created)
  }

  if (ready && !entitled) return <p className="notice">{t('pu.forbidden')}</p>
  if (!board) return <p className="hv-hint">{loading ? t('common.loading') : t('common.empty')}</p>

  return (
    <div className="hv-stack hv-stack--4">
      {/* ------------------------------------------ «Требуют заказа сейчас» */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('pu.reorder.title')}</span>
          <span className="hv-panel__aside">{t('pu.reorder.aside')}</span>
        </div>
        <div className="hv-panel__body--none">
          <table className="hv-table">
            <thead>
              <tr>
                <th>{t('pu.reorder.item')}</th>
                <th>{t('pu.reorder.kind')}</th>
                <th data-align="end">{t('pu.reorder.remaining')}</th>
                <th>{t('pu.reorder.consequence')}</th>
              </tr>
            </thead>
            <tbody>
              {board.reorder.map((row) => (
                <tr key={`${row.kind}:${row.item_code}`}>
                  <td>{row.item_name || row.item_code}</td>
                  <td>{t(kindKey(row.kind))}</td>
                  <td data-align="end" className="hv-warn">
                    {formatQuantity(row.remaining, row.unit, locale)}
                  </td>
                  <td>{consequence(row, locale)}</td>
                </tr>
              ))}
              {board.reorder.length === 0 && (
                <tr>
                  <td colSpan={4}>{t('pu.reorder.empty')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="hv-panel__foot">
          <span>{t('pu.reorder.foot')}</span>
          <button
            className="hv-btn hv-btn--sm hv-btn--primary"
            type="button"
            disabled={board.reorder.length === 0}
            onClick={() => void raise(true)}
          >
            {t('pu.reorder.seed')}
          </button>
        </div>
      </section>

      {/* -------------------------------------------------- the orders table */}
      <div className="hv-row hv-row--between">
        <FilterChips
          chips={chips}
          all={{ label: t('common.all'), count: board.total }}
          active={status}
          onSelect={(key) => setStatus(key as PurchaseStatus | null)}
          label={t('pu.filter.label')}
        />
        <div className="hv-row">
          <input
            className="hv-input"
            type="search"
            aria-label={t('pu.search')}
            placeholder={t('pu.search')}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button className="hv-btn" type="button" onClick={() => void raise(false)}>
            {t('pu.action.new')}
          </button>
        </div>
      </div>

      <div className="hv-table-wrap">
        <table className="hv-table">
          <thead>
            <tr>
              <th>{t('pu.table.number')}</th>
              <th>{t('pu.table.supplier')}</th>
              <th>{t('pu.table.composition')}</th>
              <th>{t('pu.table.status')}</th>
              <th>{t('pu.table.raised')}</th>
              <th>{t('pu.table.expected')}</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => (
              <tr key={order.id}>
                <td className="hv-table__id">
                  <button
                    className="hv-btn hv-btn--sm"
                    type="button"
                    onClick={() => void openOrder(order.id)}
                  >
                    {order.number}
                  </button>
                </td>
                {/* «не выбран» is a fact about the draft, not a missing value. */}
                <td>{order.supplier_name ?? t('pu.supplier.none')}</td>
                <td>
                  {t('pu.table.units', {
                    lines: order.line_count,
                    quantity: formatQuantity(order.total_quantity, '', locale),
                  })}
                </td>
                <td>
                  <span className="hv-state">{t(statusKey(order.status))}</span>
                </td>
                <td>{formatDay(order.created_at, locale)}</td>
                <td>{formatDay(order.expected_at, locale)}</td>
              </tr>
            ))}
            {orders.length === 0 && (
              <tr>
                <td colSpan={6}>{t('common.empty')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* --------------------------------------------------- «Поставщики» */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('pu.scores.title')}</span>
          <span className="hv-panel__aside">{t('pu.scores.aside')}</span>
        </div>
        <div className="hv-panel__body--none">
          <table className="hv-table">
            <thead>
              <tr>
                <th>{t('pu.scores.supplier')}</th>
                <th data-align="end">{t('pu.scores.deliveries')}</th>
                <th data-align="end">{t('pu.scores.on_time')}</th>
                <th>{t('pu.scores.last')}</th>
              </tr>
            </thead>
            <tbody>
              {board.suppliers.map((score) => (
                <tr key={score.id}>
                  <td>
                    <b>{score.name}</b>
                    <div className="hv-micro">
                      {score.kinds.map((kind) => t(kindKey(kind as PurchasableKind))).join(' · ')}
                    </div>
                  </td>
                  {/* Zero deliveries is a measurement, and draws as `0`. */}
                  <td data-align="end">{score.deliveries}</td>
                  <td data-align="end">{onTime(score, locale)}</td>
                  <td>{formatDay(score.last_delivery_at, locale)}</td>
                </tr>
              ))}
              {board.suppliers.length === 0 && (
                <tr>
                  <td colSpan={4}>{t('pu.scores.empty')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="hv-panel__foot">
          <span>{t('pu.scores.foot')}</span>
        </div>
      </section>

      {maySeeMoney && prices && <PriceMovements prices={prices} locale={locale} />}

      {open && (
        <OrderDetail
          order={open}
          suppliers={suppliers}
          locale={locale}
          maySeeMoney={maySeeMoney}
          onChanged={(order) => {
            setOpen(order)
            void refetch()
          }}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  )
}

/**
 * The «Последствие» cell.
 *
 * `committed_work` is the only arm this system can measure — grams the print
 * queue has already promised away — and `not_measured` is everything else. The
 * em dash is load-bearing: it is the difference between "nothing is waiting on
 * this" and "nobody knows what running out costs".
 */
function consequence(row: ReorderRow, locale: Locale): string {
  if (row.consequence.kind === 'committed_work') {
    return translate(locale, 'pu.consequence.committed', {
      jobs: row.consequence.committed_jobs ?? 0,
      grams: formatQuantity(row.consequence.committed_grams, 'g', locale),
    })
  }
  return NOT_MEASURED
}

/**
 * The «В срок» cell: the share as a percentage, and beside it the two counts
 * it was made from, so a 100% earned by one dated delivery reads as «1 из 1».
 *
 * Null is an em dash and nothing else. The server sends null when no delivery
 * carried a date, which is a supplier with no punctuality record — not a
 * supplier at 0%, and not one at 100%. Drawing either would be the flattering
 * (or damning) number CLAUDE.md §1 exists to keep off the screen.
 */
function onTime(score: SupplierScore, locale: Locale): string {
  if (score.on_time_share === null) return NOT_MEASURED
  const percent = Math.round(Number(score.on_time_share) * 100)
  const counts = translate(locale, 'pu.scores.of_dated', {
    on_time: score.on_time,
    dated: score.dated,
  })
  return `${percent}% · ${counts}`
}
