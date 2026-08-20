import type { CSSProperties } from 'react'

import type { Locale, MessageKey } from '@printorian/ui'
import { translate } from '@printorian/ui'

import { formatDay, formatGrams, formatMoney, formatTime, percentOf } from './format'
import type {
  CategorySpend,
  DayRevenue,
  FilamentBar,
  HeatRow,
  Schedule,
  StatusSlice,
} from './types'

/**
 * The 12-hour schedule strip.
 *
 * "Free at 21:10" is a fact about time, and time needs an axis — so the queue is
 * laid on one rather than listed. Bar positions are computed here from the
 * instants the server sent, because the axis is a property of *this* render's
 * window; the server sends absolute times so a client that stayed open overnight
 * cannot drift.
 */
export function ScheduleStrip({
  schedule,
  names,
  locale,
}: {
  schedule: Schedule
  /** Printer id → the name on the wall, so the row labels match the squares. */
  names: Map<string, string>
  locale: Locale
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) =>
    translate(locale, key, details)

  const start = new Date(schedule.starts_at).getTime()
  const end = new Date(schedule.ends_at).getTime()
  const span = Math.max(1, end - start)

  if (schedule.rows.length === 0) {
    return <p className="hv-hint">{t('dashboard.schedule.empty')}</p>
  }

  return (
    <div className="hv-gantt">
      <div className="hv-gantt__scale">
        <span className="hv-gantt__who">{t('dashboard.schedule.machine')}</span>
        <div className="hv-gantt__hours">
          {hourMarks(start, end).map((mark) => (
            <span key={mark}>{formatTime(new Date(mark).toISOString(), locale)}</span>
          ))}
        </div>
      </div>

      {schedule.rows.map((row) => (
        <div className="hv-gantt__row" key={row.printer_id}>
          <span className="hv-gantt__who">{names.get(row.printer_id) ?? '—'}</span>
          <div className="hv-gantt__track">
            {row.bars.map((bar) => {
              const from = new Date(bar.starts_at).getTime()
              const to = new Date(bar.ends_at).getTime()
              // Clipped to the window: a job running past the horizon should end
              // at the edge, not push the track wider than the panel.
              const left = clamp(((from - start) / span) * 100)
              const width = Math.max(1, clamp(((Math.min(to, end) - from) / span) * 100))
              return (
                <span
                  className="hv-gantt__bar"
                  key={bar.job_id}
                  data-kind={bar.status === 'printing' ? 'printing' : 'queued'}
                  style={{ left: `${left}%`, width: `${width}%` }}
                  title={barLabel(bar)}
                >
                  {barLabel(bar)}
                </span>
              )
            })}
            <span className="hv-gantt__now" style={{ left: '0%' }} />
          </div>
        </div>
      ))}
    </div>
  )
}

/**
 * What a bar says: the order, and the plate when there is one.
 *
 * The order number first, because that is what an operator is holding when they
 * come to this screen. Never the id — a bar reading `01a01a12` is unreadable, and
 * it was the first thing that went wrong when this was built from the job alone.
 */
function barLabel(bar: Schedule['rows'][number]['bars'][number]): string {
  const order = bar.order_number || bar.order_id.slice(0, 8)
  return bar.label ? `${order} · ${bar.label}` : order
}

/**
 * Even two-hour marks across the window.
 *
 * Derived rather than fixed, so the strip stays honest if the horizon is ever
 * changed on the server: the axis is drawn from the window it was given.
 */
function hourMarks(start: number, end: number): number[] {
  const step = (end - start) / 6
  return Array.from({ length: 7 }, (_, index) => start + step * index)
}

function clamp(value: number): number {
  return Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0))
}

/**
 * The filament panel: loaded / in stock / committed, per material.
 *
 * The committed column is the number nobody tracks and the one that causes the
 * stall, so it is drawn on the same bar as the mass that is actually held rather
 * than beside it — a bar whose hatched part runs past the solid one is a farm
 * that has promised filament it does not have.
 */
export function FilamentPanel({
  bars,
  locale,
}: {
  bars: FilamentBar[]
  locale: Locale
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) =>
    translate(locale, key, details)

  if (bars.length === 0) return <p className="hv-hint">{t('dashboard.filament.empty')}</p>

  return (
    <>
      <div className="hv-legend">
        <span>
          <i style={{ background: 'var(--hv-live)' }} /> {t('dashboard.filament.loaded')}
        </span>
        <span>
          <i style={{ background: 'var(--hv-text-faint)' }} /> {t('dashboard.filament.stock')}
        </span>
        <span>
          <i
            style={{
              background:
                'repeating-linear-gradient(45deg,var(--hv-warn) 0 3px,transparent 3px 6px)',
            }}
          />{' '}
          {t('dashboard.filament.committed')}
        </span>
      </div>

      {bars.map((bar) => {
        const loaded = Number(bar.loaded_grams)
        const stock = Number(bar.stock_grams)
        const committed = Number(bar.committed_grams)
        const free = Number(bar.free_grams)
        // The bar is scaled to whichever is larger — what is held, or what has
        // been promised. Scaling to the holding alone would clip the overshoot,
        // which is the one case the panel exists to show.
        const scale = Math.max(loaded + stock, committed, 1)

        return (
          <div key={bar.code}>
            <div className="hv-row hv-row--between" style={{ marginBottom: 'var(--hv-1)' }}>
              <span className="hv-h">
                <span
                  className="hv-ams"
                  style={{ display: 'inline-flex', verticalAlign: '-1px' }}
                  aria-hidden="true"
                >
                  <i style={{ '--hv-slot': bar.color_hex } as CSSProperties} />
                </span>{' '}
                {bar.name}
              </span>
              <span className={free < 0 ? 'hv-mono hv-warn' : 'hv-mono'}>
                {formatGrams(String(loaded + stock), locale)} г
              </span>
            </div>
            <div className="hv-stack-bar">
              <span data-part="loaded" style={{ width: `${percentOf(loaded, scale)}%` }} />
              <span data-part="stock" style={{ width: `${percentOf(stock, scale)}%` }} />
              <span data-part="committed" style={{ width: `${percentOf(committed, scale)}%` }} />
            </div>
            <div
              className={free < 0 ? 'hv-micro hv-warn' : 'hv-micro'}
              style={{ marginTop: '2px' }}
            >
              {free < 0
                ? t('dashboard.filament.short', { grams: formatGrams(String(-free), locale) })
                : t('dashboard.filament.free', {
                    grams: formatGrams(String(free), locale),
                    jobs: bar.committed_jobs,
                  })}
              {bar.loaded_printer_ids.length > 0 &&
                ` · ${t('dashboard.filament.machines', { count: bar.loaded_printer_ids.length })}`}
            </div>
          </div>
        )
      })}
    </>
  )
}

/**
 * A ranked bar list. Used twice: the stage funnel and the spend breakdown.
 *
 * Both answer the same kind of question — "which of these is the biggest" — and
 * comparing lengths is a glance rather than a reading task, which is the whole
 * reason neither is a table.
 */
export function Funnel({
  rows,
  locale,
}: {
  rows: { key: string; label: string; amount: number; display: string; tone?: string | undefined }[]
  locale: Locale
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const peak = Math.max(...rows.map((row) => row.amount), 0)

  if (rows.length === 0 || peak === 0) return <p className="hv-hint">{t('common.empty')}</p>

  return (
    <div className="hv-funnel">
      {rows.map((row) => (
        <div
          className="hv-funnel__row"
          key={row.key}
          {...(row.tone ? { 'data-tone': row.tone } : {})}
        >
          <span className="hv-funnel__k">{row.label}</span>
          <span className="hv-funnel__bar">
            <i style={{ width: `${percentOf(row.amount, peak)}%` }} />
          </span>
          <span className="hv-funnel__n">{row.display}</span>
        </div>
      ))}
    </div>
  )
}

export function stageRows(funnel: StatusSlice[], locale: Locale) {
  return funnel
    .filter((slice) => slice.count > 0)
    .map((slice) => ({
      key: slice.status,
      label: translate(locale, `order.status.${slice.status}` as MessageKey),
      amount: slice.count,
      display: String(slice.count),
      // The queue's own colour vocabulary: work being printed is live, work
      // waiting on a decision is a warning, everything else is neutral.
      tone:
        slice.status === 'printing'
          ? 'live'
          : slice.status === 'price_review'
            ? 'warn'
            : undefined,
    }))
}

export function spendRows(spend: CategorySpend[], locale: Locale) {
  return spend
    .filter((row) => Number(row.amount) !== 0)
    .map((row) => ({
      key: row.category,
      label: translate(locale, `dashboard.spend.${row.category}` as MessageKey),
      amount: Number(row.amount),
      display: formatMoney(row.amount, locale),
    }))
}

/**
 * Thirty days of revenue as one line.
 *
 * Drawn as an inline SVG rather than pulled from a chart library: it is a
 * polyline over a fixed viewBox, it has to survive both themes on tokens alone,
 * and the farm's LAN cannot reach a CDN anyway.
 */
export function Sparkline({ series }: { series: DayRevenue[] }) {
  const values = series.map((point) => Number(point.amount) || 0)
  const peak = Math.max(...values, 1)
  const step = values.length > 1 ? 600 / (values.length - 1) : 600
  const points = values.map((value, index) => {
    const x = Math.round(index * step)
    // 6 to 60 rather than 0 to 60: a peak touching the top edge loses its stroke
    // to the clip, and a flat run of zeroes should still sit on the floor line.
    const y = Math.round(60 - (value / peak) * 54)
    return `${x},${y}`
  })

  return (
    <svg className="hv-spark" viewBox="0 0 600 60" preserveAspectRatio="none" role="presentation">
      <polygon points={`0,60 ${points.join(' ')} 600,60`} />
      <polyline points={points.join(' ')} />
    </svg>
  )
}

/** The peak and trough annotation over the sparkline. */
export function sparkExtremes(series: DayRevenue[], locale: Locale) {
  const banked = series.filter((point) => Number(point.amount) > 0)
  if (banked.length === 0) return null
  const sorted = [...banked].sort((a, b) => Number(a.amount) - Number(b.amount))
  const low = sorted[0]!
  const high = sorted[sorted.length - 1]!
  return {
    peak: translate(locale, 'dashboard.finance.peak', {
      day: formatDay(high.day, locale),
      value: formatMoney(high.amount, locale),
    }),
    low: translate(locale, 'dashboard.finance.low', {
      day: formatDay(low.day, locale),
      value: formatMoney(low.amount, locale),
    }),
  }
}


/**
 * The week's load, hour by hour: seven rows of twenty-four cells.
 *
 * Its point is the shape rather than any one cell. A farm that runs hot every
 * afternoon and dark every night has capacity nobody is selling, and that fact
 * does not appear in a daily utilisation figure — which is exactly why the kit
 * draws this beside the figure rather than instead of it.
 *
 * Brightness is the value, carried as `--v` so the cell's own rule owns the
 * ramp. Colour would be wrong here: it means machine state everywhere else in
 * this system, and an hour is not a machine.
 */
export function HeatMap({ rows, locale }: { rows: HeatRow[]; locale: Locale }) {
  const t = (key: MessageKey) => translate(locale, key)
  if (rows.length === 0) return <p className="hv-hint">{t('dashboard.load.empty')}</p>

  return (
    <div className="hv-heat">
      {rows.map((row) => (
        <div className="hv-heat__row" key={`${row.weekday}-${row.hours.length}`}>
          <span className="hv-heat__d">
            {translate(locale, `weekday.${row.weekday}` as MessageKey)}
          </span>
          {row.hours.map((value, hour) => (
            <i
              className="hv-heat__c"
              // The hour *is* the identity here — twenty-four fixed columns, a
              // ruler rather than a list of things.
              key={`h${hour}`}
              style={{ '--v': Number(value) } as CSSProperties}
              title={`${String(hour).padStart(2, '0')}:00 · ${Math.round(Number(value) * 100)}%`}
            />
          ))}
        </div>
      ))}
    </div>
  )
}
