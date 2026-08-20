import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'

import { useLiveEvents } from '@printorian/events'
import type { LiveEvent } from '@printorian/events'
import { api, translate, useSession } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { Kpi } from '../dashboard/Kpi'
import { Metric, ParcelBoard, PickupList, ShiftPanel, TaraTable } from './Board'
import { ParcelDetail } from './ParcelDetail'
import { formatClock, formatCountdown, formatGrams, formatMoney, formatPercent } from './format'
import type { Badge, PackBoard, Parcel } from './types'

/**
 * Packing — the last post before the van.
 *
 * The screen is built round one number the rest of the console does not have: a
 * countdown to the courier. Everything in today's pickup is due at the same
 * instant regardless of when it was inspected, so the header states the clock,
 * the board sorts by it, and every card is coloured against it.
 *
 * Live and refetched rather than patched, for the reason the other boards are: a
 * parcel moving changes the columns, the tiles, the post's pace, the tara cover
 * and possibly somebody's badge, and a client that tried to patch all of that
 * would be reimplementing the read model in TypeScript.
 */

const VIEW_PRODUCTION = 'view_production'
const PACK_ORDER = 'pack_order'

/** Long enough to absorb a burst, short enough that a new parcel appears. */
const COALESCE_MS = 400

const WATCHED = ['packaging.', 'postproduction.']

export function PackagingPage({ locale }: { locale: Locale }) {
  const { actor, ready } = useSession()
  const t = useCallback(
    (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details),
    [locale],
  )

  const [board, setBoard] = useState<PackBoard | null>(null)
  const [loading, setLoading] = useState(true)
  const [openId, setOpenId] = useState<string | null>(null)

  const entitled = actor?.permissions.includes(VIEW_PRODUCTION) ?? false
  const mayPack = actor?.permissions.includes(PACK_ORDER) ?? false

  const refetch = useCallback(async () => {
    try {
      setBoard(await api.get<PackBoard>('/packaging/board'))
    } catch (exc: unknown) {
      // Keep the last board on screen rather than blanking it mid-shift.
      console.warn('packaging refresh failed', exc)
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

  useLiveEvents({ onEvent, onResync: () => void refetch(), enabled: ready && entitled })

  // The first load does not wait for the stream: a console whose socket cannot
  // connect should show a stale board with a warning, not «Загрузка…» all shift.
  useEffect(() => {
    if (ready && entitled) void refetch()
  }, [ready, entitled, refetch])

  /**
   * The open parcel, re-read from the freshly fetched board.
   *
   * Held by id rather than by value so the popup follows the server: a packer
   * ticking a step sees the next one because the board came back, not because
   * the client guessed what the server would say.
   */
  const open = useMemo<Parcel | null>(() => {
    if (openId === null) return null
    for (const column of board?.columns ?? []) {
      const found = column.tasks.find((parcel) => parcel.id === openId)
      if (found) return found
    }
    return null
  }, [board, openId])

  if (ready && !entitled) return <p className="notice">{t('pk.forbidden')}</p>
  if (!board) return <p className="hv-hint">{loading ? t('common.loading') : t('common.empty')}</p>

  const { kpi, metrics, shift, tara, pickups } = board
  const toCutoff = minutesUntil(board.next_cutoff_at, board.at)
  const lowStock = tara.filter(
    (row) => Number(row.reorder_at) > 0 && Number(row.stock) <= Number(row.reorder_at),
  ).length

  return (
    <div className="hv-stack hv-stack--4">
      {/* ------------------------------------------------------- the clock */}
      <div className="hv-frame" style={{ '--ink': 'var(--hv-warn)' } as CSSProperties}>
        <div className="hv-row hv-row--between">
          <div>
            <span className="hv-h hv-warn">
              {toCutoff === null
                ? t('pk.clock.no_van')
                : t('pk.clock.until', { time: formatCountdown(toCutoff, locale) })}
            </span>
            <p className="hv-micro" style={{ marginTop: 'var(--hv-1)' }}>
              {t('pk.clock.detail', {
                parcels: kpi.due_before_cutoff,
                pace:
                  kpi.average_minutes === null ? '—' : formatCountdown(kpi.average_minutes, locale),
              })}
            </p>
          </div>
          {/* `flex: 0 1 340px` rather than a min-width, so a phone does not get
              a 340px block inside a 343px column. */}
          <div className="hv-vs" style={{ flex: '0 1 340px' }}>
            <div>
              <span className="hv-vs__k">{t('pk.clock.queued')}</span>
              <span className="hv-vs__v">{kpi.queued}</span>
            </div>
            <div>
              <span className="hv-vs__k">{t('pk.clock.packed')}</span>
              <span className="hv-vs__v hv-good">{kpi.packed_today}</span>
            </div>
            <div>
              <span className="hv-vs__k">{t('pk.clock.slack')}</span>
              <span className="hv-vs__v">{slack(kpi, toCutoff, locale)}</span>
            </div>
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------ shift tiles */}
      <div className="hv-grid hv-grid--4">
        <Kpi
          locale={locale}
          label={t('pk.kpi.queued')}
          value={String(kpi.queued)}
          tone="live"
          // `КУРЬЕР 3`, not `3 КУРЬЕР`: Russian agrees the noun with the numeral,
          // and the kit's label-then-value idiom sidesteps it.
          note={kpi.queued_by_method
            .map(
              ([method, count]) =>
                `${translate(locale, `pk.method.${method}` as MessageKey).toUpperCase()} ${count}`,
            )
            .join(' · ')}
          foot={[
            t('pk.kpi.before_cutoff'),
            <span className={kpi.urgent > 0 ? 'hv-warn' : undefined}>{kpi.urgent}</span>,
          ]}
        />
        <Kpi
          locale={locale}
          label={t('pk.kpi.packed')}
          value={String(kpi.packed_today)}
          // The kit's line: what a parcel actually costs against what it is
          // supposed to. Yesterday's count is a comparison the tile does not
          // make — the pace in the footer is the one it does.
          note={
            kpi.average_minutes === null
              ? t('pk.kpi.yesterday', { count: kpi.packed_yesterday })
              : t('pk.kpi.average_vs_norm', {
                  average: formatCountdown(kpi.average_minutes, locale),
                  norm: formatCountdown(kpi.norm_minutes ?? '0', locale),
                })
          }
          foot={[
            t('pk.kpi.pace'),
            <span className={paceMark(kpi.pace_percent)}>
              {formatPercent(kpi.pace_percent, locale)}
            </span>,
          ]}
        />
        <Kpi
          locale={locale}
          label={t('pk.kpi.complete')}
          value={
            kpi.days_without_discrepancy === null
              ? '—'
              : formatPercent(completeness(metrics), locale)
          }
          tone={kpi.discrepancies === 0 ? 'good' : undefined}
          // Both halves, as the kit writes it. "62 дня без недовложений" on a post
          // that had one last week would be true of the streak and false of the
          // window, and the reader cannot tell which they are being told.
          note={
            kpi.days_without_discrepancy === null
              ? t('pk.kpi.nothing_shipped')
              : t('pk.kpi.clean_days', {
                  short: kpi.discrepancies,
                  days: kpi.days_without_discrepancy,
                })
          }
          foot={[t('pk.kpi.short'), <span>{kpi.discrepancies}</span>]}
        />
        <Kpi
          locale={locale}
          label={t('pk.kpi.cost')}
          value={formatMoney(kpi.cost_per_parcel, locale)}
          note={t('pk.kpi.cost_note')}
        />
      </div>

      {/* ------------------------------------------------------------ board */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('pk.board')}</span>
          <span className="hv-panel__aside">{t('pk.board.aside')}</span>
        </div>
        <div className="hv-panel__body">
          <ParcelBoard
            columns={board.columns}
            locale={locale}
            onOpen={(parcel) => setOpenId(parcel.id)}
          />
        </div>
        <div className="hv-panel__foot">
          <span>{t('pk.board.foot')}</span>
          <span>
            {t('dashboard.updated')} {formatClock(board.at, locale)}
          </span>
        </div>
      </section>

      {/* -------------------------------------------- analytics and people */}
      <section className="hv-cols hv-cols--2">
        <div className="hv-stack">
          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('pk.tara')}</span>
              <span className="hv-panel__aside">{t('pk.tara.aside')}</span>
            </div>
            <div className="hv-panel__body--none">
              <TaraTable tara={tara} locale={locale} />
            </div>
            <div className="hv-panel__foot">
              <span className={lowStock > 0 ? 'hv-warn' : undefined}>
                {t('pk.tara.low', { count: lowStock })}
              </span>
            </div>
          </section>

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('pk.metrics', { days: metrics.days })}</span>
            </div>
            <div className="hv-panel__body hv-panel__body--tight">
              <ul className="hv-leaders">
                <Metric label={t('pk.metrics.packed')} value={String(metrics.packed)} />
                <Metric
                  label={t('pk.metrics.average')}
                  value={
                    metrics.average_minutes === null
                      ? '—'
                      : formatCountdown(metrics.average_minutes, locale)
                  }
                />
                <Metric
                  label={t('pk.metrics.accuracy')}
                  value={formatPercent(metrics.tara_accuracy_percent, locale)}
                  {...(metrics.tara_accuracy_percent === null ? {} : { tone: 'good' as const })}
                />
                <Metric
                  label={t('pk.metrics.short')}
                  value={String(metrics.discrepancies)}
                  tone={metrics.discrepancies === 0 ? 'good' : 'warn'}
                />
                <Metric
                  label={t('pk.metrics.damages')}
                  value={metrics.damages === null ? '—' : String(metrics.damages)}
                />
                <Metric
                  label={t('pk.metrics.missed')}
                  value={String(metrics.missed_cutoffs)}
                  {...(metrics.missed_cutoffs > 0 ? { tone: 'warn' as const } : {})}
                />
                <Metric
                  label={t('pk.metrics.cost')}
                  value={formatMoney(metrics.cost_per_parcel, locale)}
                />
              </ul>
              <hr className="hv-hr" />
              <div className="hv-slab">
                <span>{t('pk.metrics.score')}</span>
                <span className="hv-slab__v">
                  {metrics.score === null ? '—' : `${Number(metrics.score).toFixed(1)} / 10`}
                </span>
              </div>
            </div>
            <div className="hv-panel__foot">
              <span>{t('pk.metrics.foot')}</span>
            </div>
          </section>
        </div>

        <div className="hv-stack">
          <Badges card={shift.find((one) => one.operator_id === actor?.user_id)} locale={locale} />

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('pk.people')}</span>
              <span className="hv-panel__aside">
                {t('pk.people.aside', { count: shift.length })}
              </span>
            </div>
            <div className="hv-panel__body--none">
              <ShiftPanel shift={shift} locale={locale} meId={actor?.user_id ?? null} />
            </div>
          </section>

          <section className="hv-frame">
            <span className="hv-label">{t('pk.pickups')}</span>
            <PickupList pickups={pickups} locale={locale} />
          </section>
        </div>
      </section>

      {open && (
        <ParcelDetail
          parcel={open}
          tara={tara}
          locale={locale}
          mayPack={mayPack}
          onChanged={() => void refetch()}
          onClose={() => setOpenId(null)}
        />
      )}
    </div>
  )
}

/**
 * The packer's own marks.
 *
 * Unearned badges are shown dimmed rather than hidden, so there is something
 * visible to earn — and the footer says plainly that none can be awarded by
 * hand, because a badge somebody can grant is a badge somebody can withhold.
 */
function Badges({ card, locale }: { card: { badges: Badge[] } | undefined; locale: Locale }) {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)
  const badges = card?.badges ?? []
  const earned = badges.filter((badge) => badge.tier > 0).length

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('pk.badges')}</span>
        <span className="hv-panel__aside">
          {t('pk.badges.aside', { earned, total: badges.length })}
        </span>
      </div>
      <div className="hv-panel__body">
        {badges.length === 0 ? (
          <p className="hv-hint">{t('pk.people.empty')}</p>
        ) : (
          <div className="hv-badges">
            {badges.map((badge) => (
              <div className="hv-badge" key={badge.code} data-tier={badge.tier}>
                <span className="hv-badge__g">{glyph(badge.code)}</span>
                <span className="hv-badge__k">{translate(locale, badge.code as MessageKey)}</span>
                <span className="hv-badge__n">{caption(badge, locale)}</span>
              </div>
            ))}
          </div>
        )}
        <p className="hv-micro" style={{ marginTop: 'var(--hv-3)' }}>
          {t('pk.badges.foot')}
        </p>
      </div>
    </section>
  )
}

/**
 * The mark inside a badge plate.
 *
 * Monochrome glyphs, never colour: colour means machine state everywhere in this
 * system, and a badge is not a state.
 */
function glyph(code: string): string {
  if (code === 'badge.packing.volume') return 'Σ'
  if (code === 'badge.packing.complete') return '✓'
  if (code === 'badge.packing.pace') return '↑'
  if (code === 'badge.packing.cutoffs') return 'ОТС'
  if (code === 'badge.packing.fragile') return 'ХР'
  return '·'
}

/** The figures behind a badge, folded into the reader's own language. */
function caption(badge: Badge, locale: Locale): string {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)
  if (badge.code === 'badge.packing.pace') {
    return badge.detail.pace
      ? t('pk.badge.pace_value', { pace: `${Number(badge.detail.pace).toFixed(0)}%` })
      : t('pk.badge.unearned')
  }
  if (badge.code === 'badge.packing.complete') {
    // Both halves, always. A caption reading "100 БЕЗ НЕДОВЛОЖЕНИЙ" on a badge
    // correctly unearned would contradict its own reason for being dim.
    return t('pk.badge.clean', {
      packed: badge.detail.packed ?? '0',
      short: badge.detail.short ?? '0',
    })
  }
  if (badge.code === 'badge.packing.cutoffs') {
    return t('pk.badge.cutoffs', {
      met: badge.detail.met ?? '0',
      packed: badge.detail.packed ?? '0',
    })
  }
  if (badge.code === 'badge.packing.fragile') {
    return t('pk.badge.progress', {
      done: badge.detail.wrapped ?? '0',
      next: badge.detail.next ?? '—',
    })
  }
  return t('pk.badge.progress', {
    done: badge.detail.packed ?? '0',
    next: badge.detail.next ?? '—',
  })
}

/** Minutes from the board's own instant to the next van, or `null` if none. */
function minutesUntil(cutoff: string | null, at: string): number | null {
  if (cutoff === null) return null
  const gap = new Date(cutoff).getTime() - new Date(at).getTime()
  return Number.isFinite(gap) ? gap / 60000 : null
}

/**
 * How much time is left over once the queue is packed at the current pace.
 *
 * The figure the whole header exists to produce: "2 ч 14 м to the van" only
 * means something next to "and the queue takes 40 minutes". A negative margin is
 * rendered as a shortfall rather than as a negative duration, because a minus
 * sign in front of a clock reads as arithmetic and not as a warning.
 */
function slack(kpi: PackBoard['kpi'], toCutoff: number | null, locale: Locale): string {
  if (toCutoff === null || kpi.average_minutes === null) return '—'
  const needed = Number(kpi.average_minutes) * kpi.due_before_cutoff
  const margin = toCutoff - needed
  if (!Number.isFinite(margin)) return '—'
  const rendered = formatCountdown(margin, locale)
  return margin < 0 ? `−${rendered}` : rendered
}

/** Parcels that left complete, as a percentage of those that left. */
function completeness(metrics: PackBoard['metrics']): string | null {
  if (metrics.packed === 0) return null
  const clean = Math.max(0, metrics.packed - metrics.discrepancies)
  return ((clean / metrics.packed) * 100).toFixed(1)
}

/**
 * Which way a pace figure leans.
 *
 * Under norm is unmarked rather than red: the screen's whole claim is that a
 * norm is a gauge, and colouring a packer's tile for being 4% slow would make it
 * a stick by the end of the week.
 */
function paceMark(pace: string | null): string | undefined {
  if (pace === null) return undefined
  return Number(pace) >= 100 ? 'hv-good' : undefined
}

export { formatGrams }
