import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { ApiError } from '@printorian/api-client'

import { DataTable } from '../DataTable/DataTable'
import type { Column, StatusTag } from '../DataTable/types'
import type { Locale } from '../i18n/messages'
import { translate, translateError } from '../i18n/translate'
import { PriceBreakdown } from '../pricing/PriceBreakdown'
import type { Breakdown } from '../pricing/format'
import { formatMoney } from '../pricing/format'
import { AuthPanel } from '../session/AuthPanel'
import { api, useSession } from '../session/session'

export interface OrderEvent {
  sequence: number
  from_status: string | null
  to_status: string
  reason: string
  created_at: string
}

export interface Order {
  id: string
  number: string
  status: string
  customer_email: string
  currency: string
  total: string
  sla_credit: string
  promised_at: string | null
  paid_at: string | null
  created_at: string
  price_breakdown: Breakdown
  events: OrderEvent[]
  /** Legal next states, computed by the server's state machine. */
  allowed_transitions: string[]
}

interface OrderTable {
  rows: Order[]
  counts: { status: string; count: number }[]
  total: number
}

export interface OrdersScreenProps {
  locale: Locale
  /**
   * Whose orders. `mine` is scoped by the session on the server; `all` needs
   * `view_all_orders` and is the farm's order desk.
   */
  scope: 'mine' | 'all'
  /** Heading, since "Мои заказы" and "Заказы" are different screens to a reader. */
  title: string
  /**
   * Whatever belongs under a selected order in *this* app.
   *
   * The storefront puts the queue position here; the console puts the desk that
   * advances and refunds. Injected rather than branched on a permission, because
   * the two apps do not ship each other's code — the console is never served to
   * a customer, so its refund controls are not merely hidden from one.
   *
   * `refresh` reloads the table: an order's status, its allowed transitions and
   * its credit all change together, and refetching is what keeps them consistent
   * instead of patching three fields and hoping.
   */
  renderDetail?: (order: Order, refresh: () => void) => ReactNode
}

/**
 * The orders table, in both the apps that have one.
 *
 * Built on the shared DataTable rather than a bespoke list. That component already
 * knows how to sort, count and open a detail view, and reusing it is the whole
 * reason it exists: V1 wrote this pattern four times and got four behaviours.
 *
 * Splitting the storefront from the console (ADR-0016) made this a component
 * rather than a page for the same reason. The customer cabinet and the order desk
 * differ in three things — which endpoint, one column, and what sits under a
 * selected order — and everything else about them is identical. Copying it into
 * two apps is how those three differences quietly become ten.
 */
export function OrdersScreen({ locale, scope, title, renderDetail }: OrdersScreenProps) {
  const { actor } = useSession()
  const [table, setTable] = useState<OrderTable | null>(null)
  const [selected, setSelected] = useState<Order | null>(null)
  const [error, setError] = useState<string | null>(null)
  const seesAll = scope === 'all'

  // Stable across renders so it can be a truthful hook dependency rather than
  // one the linter has to be told to ignore.
  const t = useCallback(
    (key: Parameters<typeof translate>[1]) => translate(locale, key),
    [locale],
  )

  // The endpoint differs, not just the heading — `/orders/mine` is scoped by the
  // query itself, so pointing a manager at it would quietly show them nothing.
  const load = useCallback(async () => {
    if (!actor) return
    try {
      setTable(await api.get<OrderTable>(seesAll ? '/orders' : '/orders/mine'))
      setError(null)
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : t('error.internal'),
      )
    }
  }, [actor, locale, t, seesAll])

  useEffect(() => {
    void (async () => {
      await load()
    })()
  }, [load])

  const columns = useMemo<Column<Order>[]>(
    () => [
      { key: 'number', header: t('order.number'), value: (row) => row.number },
      // Only meaningful on the all-orders table: on your own orders every row
      // would say the same thing.
      ...(seesAll
        ? [
            {
              key: 'customer',
              header: t('order.customer'),
              value: (row: Order) => row.customer_email,
            },
          ]
        : []),
      {
        key: 'status',
        header: t('order.status'),
        value: (row) => row.status,
        render: (row) => t(`order.status.${row.status}` as never),
      },
      {
        key: 'created_at',
        header: t('order.placed_at'),
        value: (row) => new Date(row.created_at),
        render: (row) => new Date(row.created_at).toLocaleDateString(locale),
      },
      {
        key: 'total',
        header: t('order.total'),
        align: 'end',
        // Sort on the number, display the formatted amount — sorting text would
        // put 1 000 before 900.
        value: (row) => Number(row.total),
        render: (row) => formatMoney(row.total, row.currency, locale),
      },
      {
        key: 'sla_credit',
        header: t('order.sla_credit'),
        align: 'end',
        value: (row) => Number(row.sla_credit),
        render: (row) =>
          Number(row.sla_credit) > 0 ? formatMoney(row.sla_credit, row.currency, locale) : '—',
      },
    ],
    [locale, t, seesAll],
  )

  const tags = useMemo<StatusTag<Order>[]>(
    () => [
      {
        key: 'awaiting_payment',
        label: t('order.status.awaiting_payment'),
        match: (row) => row.status === 'awaiting_payment',
        tone: 'warn',
      },
      {
        key: 'in_production',
        label: t('order.status.printing'),
        match: (row) =>
          ['paid', 'prep', 'queued', 'printing', 'post_production', 'quality_check'].includes(
            row.status,
          ),
      },
      {
        // The one status that needs a person and had no chip: `price_review` was
        // in none of the three above, so a held order matched no filter at all
        // and the desk had no way to ask "what is waiting on a decision?".
        key: 'price_review',
        label: t('order.status.price_review'),
        match: (row) => row.status === 'price_review',
        tone: 'warn',
      },
      {
        key: 'shipped',
        label: t('order.status.shipped'),
        match: (row) => ['shipped', 'completed'].includes(row.status),
        tone: 'good',
      },
    ],
    [t],
  )

  // A sign-in form, not just a notice telling them to sign in somewhere else.
  // Checkout used to be the only place with one, which left staff — who never
  // buy anything — with no way into the shop-floor screens at all.
  if (!actor) {
    return (
      <div className="cabinet">
        <h2>{title}</h2>
        <AuthPanel locale={locale} />
      </div>
    )
  }
  if (error) return <p className="cfg__error">{error}</p>

  return (
    <div className="cabinet">
      <h2>{title}</h2>

      <DataTable<Order>
        rows={table?.rows ?? []}
        columns={columns}
        rowKey={(row) => row.id}
        statusTags={tags}
        caption={title}
        emptyLabel={t('order.empty')}
        loadingLabel={t('common.loading')}
        isLoading={table === null}
        onRowActivate={setSelected}
      />

      {selected && (
        <section className="cabinet__detail">
          <header className="cabinet__detail-head">
            <h3>{selected.number}</h3>
            <button type="button" onClick={() => setSelected(null)}>
              ✕
            </button>
          </header>

          {Number(selected.sla_credit) > 0 && (
            <p className="cabinet__credit">
              {t('order.sla_credit')}:{' '}
              {formatMoney(selected.sla_credit, selected.currency, locale)}
            </p>
          )}

          {renderDetail?.(selected, () => {
            setSelected(null)
            void load()
          })}

          {/* The pinned breakdown — exactly what was agreed, never recomputed. */}
          <PriceBreakdown breakdown={selected.price_breakdown} locale={locale} />

          <h4>{t('order.history')}</h4>
          <ol className="cabinet__history">
            {selected.events.map((event) => (
              <li key={event.sequence}>
                <span>{t(`order.status.${event.to_status}` as never)}</span>
                <time dateTime={event.created_at}>
                  {new Date(event.created_at).toLocaleString(locale)}
                </time>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  )
}
