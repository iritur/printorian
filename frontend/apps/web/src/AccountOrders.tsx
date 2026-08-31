import { useEffect, useMemo, useState } from 'react'

import type { ChipTone, Locale } from '@printorian/ui'
import { FilterChips, api, translate } from '@printorian/ui'

import { NONE, formatMoney, shortDate } from './account'

/**
 * «Заказы» — the history, filtered by the kit's four chips.
 *
 * A summary, not the tracking screen. «Ход выполнения» sends the customer to
 * the cabinet, which owns the nine-stage pipeline, the queue position and the
 * SLA credit; duplicating any of that here would be a second rendering of the
 * same order that could disagree with the first.
 */

interface OrderRow {
  id: string
  number: string
  status: string
  total: string
  currency: string
  created_at: string
  lines: { model_name: string; quantity: number }[]
}

/**
 * The kit's four chips, over the backend's thirteen statuses.
 *
 * Grouped rather than listed: thirteen chips is not a filter, it is the state
 * machine printed sideways. The grouping is the customer's question — is it
 * coming, did it arrive, did it fall through.
 */
const GROUPS = {
  all: null,
  active: [
    'awaiting_payment',
    'paid',
    'prep',
    'price_review',
    'queued',
    'printing',
    'post_production',
    'quality_check',
    'packing',
  ],
  done: ['shipped', 'completed'],
  cancelled: ['cancelled', 'refunded'],
} as const

type Group = keyof typeof GROUPS

const GROUP_LABEL: Record<Group, string> = {
  all: 'Все',
  active: 'В работе',
  done: 'Завершены',
  cancelled: 'Отменены',
}

/** The three that are actually a filter. «Все» is the absence of one. */
const FILTERED = ['active', 'done', 'cancelled'] as const satisfies readonly Group[]

const GROUP_TONE: Partial<Record<Group, ChipTone>> = { active: 'live', done: 'good' }

/**
 * Status to the kit's `data-state`, which is what colours the chip.
 *
 * Only the four the kit defines, mapped by what the state *means* rather than
 * one-to-one: thirteen states share four colours because there are four things
 * a customer needs to tell apart — running, waiting, finished, gone wrong. The
 * caption still comes from the shared catalogue, so it stays one word per status
 * across the storefront and the console.
 */
const STATE: Record<string, string> = {
  printing: 'printing',
  paid: 'printing',
  prep: 'printing',
  post_production: 'printing',
  quality_check: 'printing',
  packing: 'printing',
  draft: 'paused',
  awaiting_payment: 'paused',
  queued: 'paused',
  price_review: 'paused',
  shipped: 'finished',
  completed: 'finished',
  cancelled: 'offline',
  refunded: 'offline',
}

export function AccountOrders({ locale, onTrack }: { locale: Locale; onTrack: () => void }) {
  const [rows, setRows] = useState<OrderRow[] | null>(null)
  const [group, setGroup] = useState<Group>('all')

  useEffect(() => {
    void api
      .get<{ rows: OrderRow[] }>('/orders/mine?limit=200')
      .then((page) => setRows(page.rows))
      .catch(() => setRows([]))
  }, [])

  const counts = useMemo(() => {
    const all = rows ?? []
    return {
      all: all.length,
      active: all.filter((row) => (GROUPS.active as readonly string[]).includes(row.status)).length,
      done: all.filter((row) => (GROUPS.done as readonly string[]).includes(row.status)).length,
      cancelled: all.filter((row) => (GROUPS.cancelled as readonly string[]).includes(row.status))
        .length,
    }
  }, [rows])

  const shown = (rows ?? []).filter((row) => {
    const wanted = GROUPS[group]
    return wanted === null || (wanted as readonly string[]).includes(row.status)
  })

  if (rows === null) return <p className="hv-hint">Загрузка…</p>

  return (
    <>
      <div className="hv-row hv-row--between">
        {/*
          «Все» is the group with no filter on it, so it goes in `all` rather
          than among the chips — which is also what gives it the shared
          component's clearing rule: pressing the chip already in force comes
          back here as `null`, and `null` is this screen's «Все».
        */}
        <FilterChips
          label="Фильтр заказов"
          all={{ label: GROUP_LABEL.all, count: counts.all }}
          chips={FILTERED.map((key) => ({
            key,
            label: GROUP_LABEL[key],
            count: counts[key],
            tone: GROUP_TONE[key],
          }))}
          active={group === 'all' ? null : group}
          onSelect={(key) => setGroup((key as Group | null) ?? 'all')}
        />
        <button className="hv-btn hv-btn--sm" type="button" onClick={onTrack}>
          Ход выполнения ›
        </button>
      </div>

      <div className="hv-table-wrap">
        <table className="hv-table">
          <thead>
            <tr>
              <th>Номер</th>
              <th>Модель</th>
              <th>Статус</th>
              <th>Создан</th>
              <th data-align="end">Сумма</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => {
              const line = row.lines[0]
              const spent = (GROUPS.cancelled as readonly string[]).includes(row.status)
              return (
                <tr key={row.id}>
                  <td className={`hv-table__id${spent ? ' hv-faint' : ''}`}>
                    {(GROUPS.active as readonly string[]).includes(row.status) ? (
                      <b>{row.number}</b>
                    ) : (
                      row.number
                    )}
                  </td>
                  <td className={spent ? 'hv-faint' : undefined}>
                    {line ? `${line.model_name} · ${line.quantity} шт` : NONE}
                  </td>
                  <td>
                    {/* The caption comes from the shared catalogue, so an order
                        never reads as two different things here and in the
                        cabinet. */}
                    <span className="hv-state" data-state={STATE[row.status] ?? 'idle'}>
                      {translate(locale, `order.status.${row.status}` as never)}
                    </span>
                  </td>
                  <td className={spent ? 'hv-faint' : undefined}>
                    {shortDate(row.created_at, locale)}
                  </td>
                  <td data-align="end" className={spent ? 'hv-faint' : undefined}>
                    {formatMoney(row.total, row.currency, locale)}
                  </td>
                </tr>
              )
            })}
            {shown.length === 0 && (
              <tr>
                <td colSpan={5} className="hv-faint">
                  {translate(locale, 'common.empty')}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="hv-panel__foot" style={{ border: '1px solid var(--hv-line)', borderTop: 0 }}>
        <span>
          ПОКАЗАНО {shown.length} ИЗ {counts.all}
        </span>
        {/*
          A plain link, not a fetch-and-blob. The route answers with
          `Content-Disposition: attachment`, so the browser saves it and names
          it — and the session cookie rides along, which a script-built download
          would have to reproduce by hand.
        */}
        <a className="hv-btn hv-btn--sm" href="/api/account/orders.csv" download>
          Выгрузить историю CSV
        </a>
      </div>
    </>
  )
}
