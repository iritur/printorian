import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useLiveEvents } from '@printorian/events'
import type { LiveEvent } from '@printorian/events'
import { api, translate, useSession } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { PrinterDetail } from '../FleetAdmin'
import type { PrinterRow } from '../FleetPage'
import { Kpi } from './Kpi'
import { AlertFeed, StatusWall } from './StatusWall'
import {
  FilamentPanel,
  Funnel,
  HeatMap,
  ScheduleStrip,
  Sparkline,
  spendRows,
  sparkExtremes,
  stageRows,
} from './Panels'
import { formatMoney, formatMoneyShort, formatNumber, formatTime } from './format'
import { PERIODS } from './types'
import type { FarmSummary, Period, WallNode } from './types'

/**
 * The farm summary — the console's first screen.
 *
 * Four questions, each given the shape that answers it: what is each machine
 * doing (the status wall), when does one free up (the schedule), will the farm
 * run out of filament (the stacked bars), and where are the orders stuck (the
 * stage funnel). The KPI tiles sit above each, and every one of them carries its
 * previous-period figure — the server computes the comparison so the two windows
 * are always the same length.
 *
 * ## Why events refetch instead of patching
 *
 * The same reasoning as the fleet table, one level up: an event says "you are
 * out of date", not "here is the new truth". Almost nothing on this screen is a
 * field of the event that changed it — a printer going idle moves utilisation,
 * the schedule, the run hours and possibly an alert — so a client that patched
 * would be reimplementing the whole read model in TypeScript. Bursts are
 * coalesced, because a farm restarting produces a dozen events in a second and
 * that is one refetch.
 */

const VIEW_ALL_ORDERS = 'view_all_orders'
const MANAGE_FLEET = 'manage_fleet'

/** Long enough to absorb a burst, short enough to feel immediate. */
const COALESCE_MS = 400

/**
 * Event families that can change something on this screen.
 *
 * Listed rather than "refetch on anything": the console holds one stream for
 * every open screen, and a journal post being published should not re-read the
 * farm's finances.
 */
const WATCHED = ['fleet.', 'job.', 'order.', 'printer.', 'payment.']

export function DashboardPage({
  locale,
  onOpenOrders,
}: {
  locale: Locale
  /** The order desk, for the "Диспетчерская ›" link. */
  onOpenOrders?: () => void
}) {
  const { actor, ready } = useSession()
  const t = useCallback(
    (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details),
    [locale],
  )

  const [period, setPeriod] = useState<Period>('today')
  const [summary, setSummary] = useState<FarmSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<PrinterRow | null>(null)
  const entitled = actor?.permissions.includes(VIEW_ALL_ORDERS) ?? false
  const mayManage = actor?.permissions.includes(MANAGE_FLEET) ?? false

  // Read through a ref so the refetch callback does not change identity with the
  // period — otherwise every period change would tear down the event stream.
  const periodRef = useRef(period)
  useEffect(() => {
    periodRef.current = period
  }, [period])

  const refetch = useCallback(async () => {
    try {
      setSummary(await api.get<FarmSummary>(`/dashboard?period=${periodRef.current}`))
    } catch (exc: unknown) {
      // Keep the last summary on screen rather than blanking it; the connection
      // badge already says the view may be stale.
      console.warn('dashboard refresh failed', exc)
    } finally {
      setLoading(false)
    }
  }, [])

  const pending = useRef<number | null>(null)
  useEffect(() => () => window.clearTimeout(pending.current ?? undefined), [])

  const invalidate = useCallback(() => {
    if (pending.current !== null) return
    pending.current = window.setTimeout(() => {
      pending.current = null
      void refetch()
    }, COALESCE_MS)
  }, [refetch])

  const onEvent = useCallback(
    (event: LiveEvent) => {
      if (WATCHED.some((prefix) => event.name.startsWith(prefix))) invalidate()
    },
    [invalidate],
  )

  const status = useLiveEvents({
    onEvent,
    onResync: () => void refetch(),
    enabled: ready && entitled,
  })

  // A period change is a new question, not a stale view: fetch immediately
  // rather than waiting for the next event.
  useEffect(() => {
    if (ready && entitled) void refetch()
  }, [period, ready, entitled, refetch])

  /** Printer id → name, so the schedule's row labels match the wall's squares. */
  const names = useMemo(() => {
    const found = new Map<string, string>()
    for (const zone of summary?.fleet.zones ?? []) {
      for (const node of zone.nodes) found.set(node.id, node.name)
    }
    return found
  }, [summary])

  const openPrinter = useCallback(async (printerId: string) => {
    // The wall carries only what a square needs to be drawn. The popup wants the
    // whole machine — AMS slots, the service card, amortization — so it is read
    // when it is opened rather than shipped for twelve machines nobody clicked.
    try {
      setSelected(await api.get<PrinterRow>(`/printers/${printerId}`))
    } catch (exc: unknown) {
      console.warn('printer detail failed', exc)
    }
  }, [])

  if (ready && !entitled) return <p className="notice">{t('dashboard.forbidden')}</p>
  if (!summary) return <p className="hv-hint">{loading ? t('common.loading') : t('common.empty')}</p>

  const { orders, finance, fleet, schedule, filament, alerts, wait_list: waitList } = summary
  const extremes = sparkExtremes(finance.revenue_by_day, locale)
  const received = formatMoneyShort(finance.received.value, locale)
  const spend = formatMoneyShort(finance.spend.value, locale)
  const profit = formatMoneyShort(finance.profit.value, locale)
  const freeMachine = firstFree(schedule, names)

  return (
    <div className="hv-stack hv-stack--4">
      {/* ------------------------------------------------------ period switch */}
      <div className="hv-row hv-row--between">
        <div className="hv-row">
          <span className="hv-label" style={{ margin: 0 }}>
            {t('dashboard.period')}
          </span>
          <span className="hv-seg" role="group" aria-label={t('dashboard.period')}>
            {PERIODS.map((candidate) => (
              <button
                className="hv-seg__btn"
                type="button"
                key={candidate}
                aria-pressed={candidate === period}
                onClick={() => setPeriod(candidate)}
              >
                {t(`dashboard.period.${candidate}` as MessageKey)}
              </button>
            ))}
          </span>
        </div>
        <span className="hv-micro" role="status">
          {t('dashboard.compare')} · {t('dashboard.updated')} {formatTime(summary.at, locale)}
          {status !== 'live' && ` · ${t('fleet.reconnecting')}`}
        </span>
      </div>

      {/* ------------------------------------------------------------ orders */}
      <section>
        <div className="hv-row" style={{ marginBottom: 'var(--hv-2)' }}>
          <h2 className="hv-h">{t('dashboard.orders')}</h2>
          <span className="hv-micro">{t('dashboard.orders.note')}</span>
          <span className="hv-spacer" />
          {onOpenOrders && (
            <button className="hv-btn hv-btn--sm" type="button" onClick={onOpenOrders}>
              {t('dashboard.orders.desk')}
            </button>
          )}
        </div>
        <div className="hv-grid hv-grid--4">
          <Kpi
            locale={locale}
            label={t('dashboard.orders.placed')}
            value={formatNumber(orders.placed.value, locale)}
            trend={orders.placed}
            polarity="more_is_better"
            note={comparison(orders.placed.previous, locale, t)}
            foot={[
              t('dashboard.orders.paid', { count: orders.paid }),
              t('dashboard.orders.awaiting', { count: orders.awaiting_payment }),
            ]}
          />
          <Kpi
            locale={locale}
            label={t('dashboard.orders.month')}
            value={formatNumber(orders.placed_month.value, locale)}
            trend={orders.placed_month}
            polarity="more_is_better"
            note={t('dashboard.orders.last_month', {
              value: formatNumber(orders.placed_month.previous, locale),
            })}
          />
          <Kpi
            locale={locale}
            label={t('dashboard.orders.in_progress')}
            value={formatNumber(orders.in_progress, locale)}
            tone="live"
            foot={[
              t('dashboard.orders.wait_list'),
              <span className={waitList > 0 ? 'hv-warn' : undefined}>{waitList}</span>,
            ]}
          />
          <Kpi
            locale={locale}
            label={t('dashboard.orders.average')}
            value={formatNumber(orders.average_order.value, locale)}
            unit="₽"
            trend={orders.average_order}
            polarity="more_is_better"
            note={t('dashboard.orders.median', {
              value: formatMoney(orders.median_order, locale),
            })}
            foot={[t('dashboard.orders.lines'), formatNumber(orders.lines_per_order, locale, 1)]}
          />
        </div>
      </section>

      {/* ------------------------------------------------- wall + attention */}
      <section className="hv-cols hv-cols--2">
        <div className="hv-panel">
          <div className="hv-panel__head">
            <span>{t('dashboard.fleet')}</span>
            <span className="hv-panel__aside">{t('dashboard.fleet.hint')}</span>
          </div>
          <div className="hv-panel__body hv-stack">
            <div className="hv-legend">
              {/* Only states the farm is actually in. A legend listing
                  «ПАУЗА 0 · ЗАВЕРШЕНО 0» spends the reader's attention on
                  machines that do not exist. */}
              {fleet.counts
                .filter((count) => count.count > 0)
                .map((count) => (
                  <span key={count.state}>
                    <i style={{ background: stateColour(count.state) }} />
                    {translate(locale, `printer.state.${count.state}` as MessageKey)} {count.count}
                  </span>
                ))}
            </div>
            <StatusWall
              zones={fleet.zones}
              locale={locale}
              onOpen={(node: WallNode) => void openPrinter(node.id)}
            />
          </div>
          <div className="hv-panel__foot">
            <span>{t('dashboard.fleet.foot')}</span>
            <span>{t('dashboard.fleet.machines', { count: fleet.total })}</span>
          </div>
        </div>

        <div className="hv-stack">
          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('dashboard.alerts')}</span>
              <span className="hv-panel__aside">{alerts.length}</span>
            </div>
            <AlertFeed
              alerts={alerts}
              locale={locale}
              onOpen={(printerId) => void openPrinter(printerId)}
            />
          </section>

          <div className="hv-grid hv-grid--2">
            <Kpi
              locale={locale}
              label={t('dashboard.fleet.utilisation')}
              value={formatNumber(fleet.utilisation_percent, locale, 1)}
              unit="%"
              tone="live"
              note={t('dashboard.fleet.printing_of', {
                printing: fleet.printing,
                total: fleet.total,
              })}
            />
            <Kpi
              locale={locale}
              label={t('dashboard.fleet.efficiency')}
              value={
                fleet.throughput.success_percent === null
                  ? '—'
                  : formatNumber(fleet.throughput.success_percent, locale, 1)
              }
              unit={fleet.throughput.success_percent === null ? undefined : '%'}
              note={
                fleet.throughput.success_percent === null
                  ? t('dashboard.fleet.nothing_finished')
                  : t('dashboard.fleet.success', { failed: fleet.throughput.failed })
              }
            />
            <Kpi
              locale={locale}
              label={t('dashboard.fleet.run_hours')}
              value={formatNumber(fleet.throughput.run_hours, locale, 1)}
              unit={locale === 'ru' ? 'Ч' : 'H'}
              note={t('dashboard.fleet.of_capacity', {
                hours: formatNumber(fleet.throughput.capacity_hours, locale),
              })}
              {...(fleet.throughput.truncated
                ? { foot: [t('dashboard.fleet.truncated'), ''] as [string, string] }
                : {})}
            />
            <Kpi
              locale={locale}
              label={t('dashboard.fleet.idle')}
              value={formatNumber(fleet.throughput.idle_hours, locale, 1)}
              unit={locale === 'ru' ? 'Ч' : 'H'}
              tone="warn"
            />
          </div>
        </div>
      </section>

      {/* ----------------------------------------------------------- finance */}
      <section>
        <div className="hv-row" style={{ marginBottom: 'var(--hv-2)' }}>
          <h2 className="hv-h">{t('dashboard.finance')}</h2>
          <span className="hv-spacer" />
          <span className="hv-micro">{t('dashboard.finance.note')}</span>
        </div>

        <div className="hv-cols hv-cols--2">
          <div className="hv-grid hv-grid--3">
            <Kpi
              locale={locale}
              label={t('dashboard.finance.received')}
              value={received.value}
              unit={received.unit}
              trend={finance.received}
              polarity="more_is_better"
              foot={[
                t('dashboard.finance.today'),
                <span className="hv-hot">{formatMoney(finance.received_today, locale)}</span>,
              ]}
            />
            <Kpi
              locale={locale}
              label={t('dashboard.finance.spend')}
              value={spend.value}
              unit={spend.unit}
              trend={finance.spend}
              // Direction is not sentiment: spend rising is red.
              polarity="less_is_better"
              foot={[t('dashboard.finance.today'), formatMoney(finance.spend_today, locale)]}
            />
            <Kpi
              locale={locale}
              label={t('dashboard.finance.profit')}
              value={profit.value}
              unit={profit.unit}
              trend={finance.profit}
              polarity="more_is_better"
              tone="good"
              foot={[
                t('dashboard.finance.margin'),
                <span className="hv-hot">
                  {formatNumber(finance.margin_percent, locale, 1)}%
                </span>,
              ]}
            />
          </div>

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('dashboard.finance.where')}</span>
              <span className="hv-panel__aside">
                {formatMoney(finance.spend.value, locale)}
              </span>
            </div>
            <div className="hv-panel__body hv-panel__body--tight">
              <Funnel rows={spendRows(finance.spend_by_category, locale)} locale={locale} />
            </div>
            <div className="hv-panel__foot">
              <span>
                {t('dashboard.finance.refunds', {
                  count: finance.refund_count,
                  value: formatMoney(finance.refund_total, locale),
                })}
              </span>
              <span>
                {t('dashboard.finance.receivable', {
                  value: formatMoney(finance.receivable, locale),
                })}
              </span>
            </div>
          </section>
        </div>

        <div className="hv-frame" style={{ marginTop: 'var(--hv-3)' }}>
          <div className="hv-row hv-row--between" style={{ marginBottom: 'var(--hv-2)' }}>
            <span className="hv-label">{t('dashboard.finance.revenue')}</span>
            <span className="hv-micro">
              {extremes ? `${extremes.peak} · ${extremes.low}` : t('dashboard.finance.quiet')}
            </span>
          </div>
          <Sparkline series={finance.revenue_by_day} />
        </div>
      </section>

      {/* ---------------------------------------------------------- schedule */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('dashboard.schedule')}</span>
          <span className="hv-panel__aside">{t('dashboard.schedule.aside')}</span>
        </div>
        <div className="hv-panel__body hv-gantt-scroll">
          <ScheduleStrip schedule={schedule} names={names} locale={locale} />
        </div>
        <div className="hv-panel__foot">
          <div className="hv-legend">
            <span>
              <i style={{ background: 'var(--hv-live)' }} /> {t('dashboard.schedule.printing')}
            </span>
            <span>
              <i style={{ border: '1px dashed var(--hv-line)' }} />{' '}
              {t('dashboard.schedule.queued')}
            </span>
          </div>
          <span>
            {freeMachine
              ? t('dashboard.schedule.free_first', {
                  name: freeMachine.name,
                  time: formatTime(freeMachine.at, locale),
                })
              : t('dashboard.schedule.all_busy')}
          </span>
        </div>
      </section>

      {/* -------------------------------------------- filament + stages + load
          The kit's last section: the material question on the left, and on the
          right the two shapes that answer "where is the work" — which stage the
          queue is piling up in, and which hours of the week the farm is idle. */}
      <section className="hv-cols hv-cols--2">
        <div className="hv-panel">
          <div className="hv-panel__head">
            <span>{t('dashboard.filament')}</span>
            <span className="hv-panel__aside">{t('dashboard.filament.aside')}</span>
          </div>
          <div className="hv-panel__body hv-stack">
            <FilamentPanel bars={filament} locale={locale} />
          </div>
        </div>

        <div className="hv-stack">
          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('dashboard.orders.stages')}</span>
              <span className="hv-panel__aside">
                {t('dashboard.orders.stages_aside', { count: orders.in_progress })}
              </span>
            </div>
            <div className="hv-panel__body hv-panel__body--tight">
              <Funnel rows={stageRows(orders.funnel, locale)} locale={locale} />
            </div>
            <div className="hv-panel__foot">
              <span>{bottleneck(orders.funnel, locale)}</span>
            </div>
          </section>

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('dashboard.load')}</span>
              <span className="hv-panel__aside">{t('dashboard.load.aside')}</span>
            </div>
            <div className="hv-panel__body hv-heat-scroll">
              <HeatMap rows={fleet.hourly_load} locale={locale} />
            </div>
            <div className="hv-panel__foot">
              <span>{t('dashboard.load.foot')}</span>
              <span>{t('dashboard.load.night', { percent: nightLoad(fleet.hourly_load) })}</span>
            </div>
          </section>
        </div>
      </section>

      {selected && (
        <PrinterDetail
          printer={selected}
          locale={locale}
          mayManage={mayManage}
          onClose={() => setSelected(null)}
          onChanged={(updated) => {
            setSelected(updated)
            void refetch()
          }}
        />
      )}
    </div>
  )
}

/**
 * The stage with the most orders in it, for the funnel's footer.
 *
 * The kit states the bottleneck rather than leaving the reader to compare eight
 * bars, which is the whole reason the panel is bars and not a table.
 */
function bottleneck(funnel: FarmSummary['orders']['funnel'], locale: Locale): string {
  const worst = funnel.reduce<FarmSummary['orders']['funnel'][number] | null>(
    (top, slice) => (top === null || slice.count > top.count ? slice : top),
    null,
  )
  if (!worst || worst.count === 0) return translate(locale, 'dashboard.orders.no_bottleneck')
  return translate(locale, 'dashboard.orders.bottleneck', {
    stage: translate(locale, `order.status.${worst.status}` as MessageKey),
  })
}

/**
 * How busy the farm is between midnight and six, as a percentage of its day.
 *
 * The figure the load map exists to produce: machines that sit idle every night
 * are capacity nobody is selling, and it does not show up in a daily total.
 */
function nightLoad(rows: FarmSummary['fleet']['hourly_load']): string {
  if (rows.length === 0) return '0'
  let night = 0
  let all = 0
  for (const row of rows) {
    row.hours.forEach((value, hour) => {
      const load = Number(value)
      all += load
      if (hour < 6) night += load
    })
  }
  if (all === 0) return '0'
  return ((night / all) * 100).toFixed(0)
}

/**
 * The soonest machine to come free, for the schedule's footer line.
 *
 * Machines with nothing queued are absent from the strip entirely, so this is
 * about the ones that are working — "which of the busy machines finishes first"
 * — and a farm with idle machines already says so on the wall.
 */
function firstFree(
  schedule: FarmSummary['schedule'],
  names: Map<string, string>,
): { name: string; at: string } | null {
  let best: { name: string; at: string } | null = null
  for (const row of schedule.rows) {
    if (!row.free_at) continue
    if (best === null || row.free_at < best.at) {
      best = { name: names.get(row.printer_id) ?? '—', at: row.free_at }
    }
  }
  return best
}

/**
 * Colour is machine state, so the legend's swatches read from the same tokens
 * the squares do rather than from a second table that could drift.
 */
function stateColour(state: string): string {
  if (state === 'printing' || state === 'preparing') return 'var(--hv-live)'
  if (state === 'idle' || state === 'finished') return 'var(--hv-good)'
  if (state === 'paused' || state === 'maintenance') return 'var(--hv-warn)'
  if (state === 'error') return 'var(--hv-bad)'
  return 'var(--hv-line)'
}

/** "БЫЛО 11", or a note that there is nothing to compare against. */
function comparison(
  previous: string,
  locale: Locale,
  t: (key: MessageKey, details?: Record<string, unknown>) => string,
): string {
  const value = Number(previous)
  if (!Number.isFinite(value) || value === 0) return t('dashboard.no_comparison')
  return `${t('dashboard.previous')} ${formatNumber(value, locale)}`
}
