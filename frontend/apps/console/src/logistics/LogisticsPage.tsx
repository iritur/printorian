import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import { api, translate, translateError, useChrome, useSession } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { ShipmentDetail } from './ShipmentDetail'
import { formatDays, formatShare, formatStamp, statusKey } from './format'
import type { LogisticsBoard, Scorecards, Shipment } from './types'

/**
 * «Логистика»: the parcel after the post — out, in trouble, lately arrived — and
 * the two scorecards computed from where parcels actually went.
 *
 * **What the kit draws that this does not, and why**, so the next reader does
 * not port it:
 * - «Отгрузка сегодня» — parcels waiting for the van are the packaging board's
 *   («Упаковка»), and a second copy here would drift from it.
 * - «Средняя цена» on a carrier and «Что и почём» on a parcel — money, behind
 *   `VIEW_FINANCIALS`, not served by `/logistics`.
 * - «Оценка» — a composite over on-time, damage and price whose weights nobody
 *   chose.
 * - «Зоны и тарифы» — the settings table already edits and shows them (§2.1).
 * - «Возвраты 1.6%» and «География» — a return rate needs a delivered
 *   denominator the scorecard already states; the map is decoration.
 *
 * Two reads, both `VIEW_PRODUCTION`; the controls to record an event are
 * behind `mayPack`, the packaging desk's own permission.
 */

const VIEW_PRODUCTION = 'view_production'
const PACK_ORDER = 'pack_order'

type Lane = 'in_transit' | 'problems' | 'closed'
const LANES: readonly Lane[] = ['in_transit', 'problems', 'closed']

function laneKey(lane: Lane): MessageKey {
  return `lg.lane.${lane}` as MessageKey
}

export function LogisticsPage({ locale }: { locale: Locale }) {
  const { actor, ready } = useSession()
  const t = useCallback(
    (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details),
    [locale],
  )

  const [board, setBoard] = useState<LogisticsBoard | null>(null)
  const [scores, setScores] = useState<Scorecards | null>(null)
  const [open, setOpen] = useState<Shipment | null>(null)
  const [error, setError] = useState<string | null>(null)

  const entitled = actor?.permissions.includes(VIEW_PRODUCTION) ?? false
  const mayPack = actor?.permissions.includes(PACK_ORDER) ?? false

  const describe = useCallback(
    (exc: unknown) =>
      exc instanceof ApiError
        ? translateError(locale, { code: exc.code, details: exc.details })
        : translate(locale, 'error.internal'),
    [locale],
  )

  const refetch = useCallback(async () => {
    try {
      const [nextBoard, nextScores] = await Promise.all([
        api.get<LogisticsBoard>('/logistics/board'),
        api.get<Scorecards>('/logistics/scorecards'),
      ])
      setBoard(nextBoard)
      setScores(nextScores)
      setError(null)
    } catch (exc: unknown) {
      setError(describe(exc))
    }
  }, [describe])

  useEffect(() => {
    if (!ready || !entitled) return
    void (async () => {
      await refetch()
    })()
  }, [ready, entitled, refetch])

  useChrome(
    board === null
      ? null
      : {
          path: '/LOGISTICS/SHIPMENTS',
          meta: [
            { label: 'OUT', value: String(board.in_transit.length) },
            { label: 'PROBLEMS', value: String(board.problems.length) },
          ],
        },
  )

  const openShipment = async (id: string) => {
    try {
      setOpen(await api.get<Shipment>(`/logistics/shipments/${id}`))
    } catch (exc: unknown) {
      setError(describe(exc))
    }
  }

  if (ready && !entitled) return <p className="notice">{t('lg.forbidden')}</p>
  if (!board) return <p className="hv-hint">{error ?? t('common.loading')}</p>

  return (
    <div className="hv-stack hv-stack--4">
      {error && (
        <p className="hv-hint hv-bad" role="alert">
          {error}
        </p>
      )}

      {/* ------------------------------------------------------- the board */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('lg.board.title')}</span>
          <span className="hv-panel__aside">{t('lg.board.aside')}</span>
        </div>
        <div className="hv-panel__body hv-stack">
          {LANES.map((lane) => {
            const shipments = board[lane]
            return (
              <div key={lane} className="hv-lane" data-lane={lane}>
                <div className="hv-lane__head">
                  <span>{t(laneKey(lane))}</span>
                  <span className="hv-lane__count">{shipments.length}</span>
                </div>
                {shipments.length === 0 ? (
                  <p className="hv-micro">{t('lg.lane.empty')}</p>
                ) : (
                  <ul className="hv-cards">
                    {shipments.map((shipment) => (
                      <li key={shipment.id} className="hv-card" data-status={shipment.status}>
                        <button
                          type="button"
                          className="hv-card__button"
                          onClick={() => void openShipment(shipment.id)}
                        >
                          <span className="hv-card__id">{shipment.order_number}</span>
                          <span className="hv-card__meta">
                            {shipment.transit_days === null
                              ? t('lg.card.out', { days: formatDays(shipment.days_out, locale) })
                              : formatDays(shipment.transit_days, locale)}
                          </span>
                          <span className="hv-card__title">
                            {shipment.carrier_code || t('lg.card.no_carrier')}
                            {shipment.zone_code && ` · ${shipment.zone_code}`}
                          </span>
                          <span className="hv-micro">
                            {t(statusKey(shipment.status))}
                            {shipment.promised_days !== null &&
                              ` · ${t('lg.card.promised', { days: shipment.promised_days })}`}
                            {shipment.on_time === false && ` · ${t('lg.detail.late')}`}
                            {' · '}
                            {formatStamp(shipment.shipped_at, locale)}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )
          })}
        </div>
        <div className="hv-panel__foot">
          <span>{t('lg.board.foot')}</span>
        </div>
      </section>

      {/* ------------------------------------------------- «Перевозчики» */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('lg.carriers.title')}</span>
          <span className="hv-panel__aside">{t('lg.carriers.aside')}</span>
        </div>
        <div className="hv-panel__body--none">
          <table className="hv-table">
            <thead>
              <tr>
                <th>{t('lg.carriers.carrier')}</th>
                <th data-align="end">{t('lg.carriers.shipments')}</th>
                <th data-align="end">{t('lg.carriers.on_time')}</th>
                <th data-align="end">{t('lg.carriers.damaged')}</th>
                <th data-align="end">{t('lg.carriers.mean')}</th>
              </tr>
            </thead>
            <tbody>
              {(scores?.carriers ?? []).map((row) => (
                <tr key={row.carrier_code}>
                  <td>{row.carrier_code || t('lg.card.no_carrier')}</td>
                  <td data-align="end">{row.shipments}</td>
                  {/* Share over the promised deliveries, with the counts beside it. */}
                  <td data-align="end">
                    {formatShare(row.on_time_share)}
                    {row.promised > 0 &&
                      ` (${t('lg.carriers.of', { on_time: row.on_time, promised: row.promised })})`}
                  </td>
                  <td data-align="end">{row.damaged}</td>
                  <td data-align="end">{formatDays(row.mean_transit_days, locale)}</td>
                </tr>
              ))}
              {(scores?.carriers ?? []).length === 0 && (
                <tr>
                  <td colSpan={5}>{t('common.empty')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="hv-panel__foot">
          <span>{t('lg.carriers.foot')}</span>
        </div>
      </section>

      {/* --------------------------------------------- «Сроки доставки» */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('lg.accuracy.title')}</span>
          <span className="hv-panel__aside">{t('lg.accuracy.aside')}</span>
        </div>
        <div className="hv-panel__body--none">
          <table className="hv-table">
            <thead>
              <tr>
                <th>{t('lg.accuracy.zone')}</th>
                <th data-align="end">{t('lg.accuracy.promised')}</th>
                <th data-align="end">{t('lg.accuracy.actual')}</th>
                <th data-align="end">{t('lg.accuracy.accuracy')}</th>
              </tr>
            </thead>
            <tbody>
              {(scores?.zones ?? []).map((row) => (
                <tr key={`${row.zone_code}:${row.promised_days}`}>
                  <td>{row.zone_code}</td>
                  <td data-align="end">{formatDays(row.promised_days, locale)}</td>
                  <td data-align="end">{formatDays(row.mean_transit_days, locale)}</td>
                  <td data-align="end">
                    {formatShare(row.accuracy)}
                    {` (${t('lg.carriers.of', { on_time: row.on_time, promised: row.delivered })})`}
                  </td>
                </tr>
              ))}
              {(scores?.zones ?? []).length === 0 && (
                <tr>
                  <td colSpan={4}>{t('lg.accuracy.empty')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="hv-panel__foot">
          <span>{t('lg.accuracy.foot')}</span>
        </div>
      </section>

      {open && (
        <ShipmentDetail
          shipment={open}
          locale={locale}
          mayPack={mayPack}
          onChanged={(shipment) => {
            setOpen(shipment)
            void refetch()
          }}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  )
}
