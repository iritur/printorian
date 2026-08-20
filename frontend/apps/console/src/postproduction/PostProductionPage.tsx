import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useLiveEvents } from '@printorian/events'
import type { LiveEvent } from '@printorian/events'
import { api, translate, useSession } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { Kpi } from '../dashboard/Kpi'
import { Blocks, Consumables, OperationsTable, OutputSpark, ShiftPanel, TaskBoard } from './Board'
import { TaskDetail } from './TaskDetail'
import { formatClock, formatDay, formatMinutes, formatPercent } from './format'
import type { Board, Task } from './types'

/**
 * Post-production — an operator's shift.
 *
 * The screen answers three questions in the order the floor asks them: what do I
 * do next (the board, ordered by the customer's promise), how do I do it (the
 * instruction, with a time norm on every step), and how am I doing (the
 * scorecard and the marks, every figure of which is a recorded fact).
 *
 * Live for the same reason the fleet table is, and refetched rather than patched
 * for the same reason: a task moving changes the columns, the tiles, the shop's
 * pace and possibly somebody's badge, and a client that tried to patch all that
 * would be reimplementing the read model in TypeScript.
 */

const VIEW_PRODUCTION = 'view_production'
const ADVANCE = 'advance_postproduction'
const RECORD_QC = 'record_qc'

/** Long enough to absorb a burst, short enough that a finished print appears. */
const COALESCE_MS = 400

const WATCHED = ['postproduction.', 'job.']

export function PostProductionPage({ locale }: { locale: Locale }) {
  const { actor, ready } = useSession()
  const t = useCallback(
    (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details),
    [locale],
  )

  const [board, setBoard] = useState<Board | null>(null)
  const [loading, setLoading] = useState(true)
  const [openId, setOpenId] = useState<string | null>(null)

  const entitled = actor?.permissions.includes(VIEW_PRODUCTION) ?? false
  const mayAdvance = actor?.permissions.includes(ADVANCE) ?? false
  const mayInspect = actor?.permissions.includes(RECORD_QC) ?? false

  const refetch = useCallback(async () => {
    try {
      setBoard(await api.get<Board>('/postproduction/board'))
    } catch (exc: unknown) {
      // Keep the last board on screen rather than blanking it mid-shift.
      console.warn('post-production refresh failed', exc)
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

  useLiveEvents({
    onEvent,
    onResync: () => void refetch(),
    enabled: ready && entitled,
  })

  // The first load does not wait for the stream. `onResync` fires on connect and
  // would cover it, but that makes the board's arrival conditional on a
  // WebSocket — and a console whose socket cannot connect should show a stale
  // board with a warning, not «Загрузка…» for the whole shift.
  useEffect(() => {
    if (ready && entitled) void refetch()
  }, [ready, entitled, refetch])

  /**
   * The open task, re-read from the freshly fetched board.
   *
   * Held by id rather than by value so the panel follows the server: an operator
   * ticking a step sees the next one appear because the board came back, not
   * because the client guessed what the server would say.
   */
  const open = useMemo<Task | null>(() => {
    if (openId === null) return null
    for (const column of board?.columns ?? []) {
      const found = column.tasks.find((task) => task.id === openId)
      if (found) return found
    }
    return null
  }, [board, openId])

  if (ready && !entitled) return <p className="notice">{t('pp.forbidden')}</p>
  if (!board) return <p className="hv-hint">{loading ? t('common.loading') : t('common.empty')}</p>

  const { kpi, operations, shift, consumables, output_by_day: output } = board
  const best = output.reduce<[string, number] | null>(
    (top, entry) => (top === null || entry[1] > top[1] ? entry : top),
    null,
  )
  const average =
    output.length > 0 ? output.reduce((sum, entry) => sum + entry[1], 0) / output.length : 0

  return (
    <div className="hv-stack hv-stack--4">
      {/* ------------------------------------------------------ shift tiles */}
      <div className="hv-grid hv-grid--4">
        <Kpi
          locale={locale}
          label={t('pp.kpi.queued')}
          value={String(kpi.queued)}
          tone="live"
          // `ОКРАСКА 3`, not `3 ОКРАСКА`: Russian agrees the noun with the
          // numeral, and the kit's own label-then-value idiom sidesteps it.
          note={kpi.queued_by_kind
            .map(
              ([kind, count]) =>
                `${translate(locale, `pp.kind.${kind}` as MessageKey).toUpperCase()} ${count}`,
            )
            .join(' · ')}
          foot={[
            t('pp.kpi.urgent'),
            <span className={kpi.urgent > 0 ? 'hv-bad' : undefined}>{kpi.urgent}</span>,
          ]}
        />
        <Kpi
          locale={locale}
          label={t('pp.kpi.done_today')}
          value={String(kpi.completed_today)}
          note={t('pp.kpi.yesterday', { count: kpi.completed_yesterday })}
        />
        <Kpi
          locale={locale}
          label={t('pp.kpi.quality')}
          value={kpi.quality_percent === null ? '—' : formatPercent(kpi.quality_percent, locale)}
          tone={kpi.quality_percent === null ? undefined : 'good'}
          note={
            kpi.quality_percent === null
              ? t('pp.kpi.nothing_done')
              : t('pp.kpi.returns', { count: kpi.returns })
          }
        />
        <Kpi
          locale={locale}
          label={t('pp.kpi.pace')}
          value={kpi.pace_percent === null ? '—' : formatPercent(kpi.pace_percent, locale)}
          note={t('pp.kpi.shop')}
        />
      </div>

      {/* ------------------------------------------------------------ board */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('pp.board')}</span>
          <span className="hv-panel__aside">{t('pp.board.aside')}</span>
        </div>
        <div className="hv-panel__body">
          <TaskBoard
            columns={board.columns}
            locale={locale}
            onOpen={(task) => setOpenId(task.id)}
          />
        </div>
        <div className="hv-panel__foot">
          <span>{t('pp.board.foot')}</span>
          <span>
            {t('dashboard.updated')} {formatClock(board.at, locale)}
          </span>
        </div>
      </section>

      {/* ------------------------------------------- analytics and people */}
      <section className="hv-cols hv-cols--2">
        <div className="hv-stack">
          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('pp.ops')}</span>
              <span className="hv-panel__aside">{t('pp.ops.aside')}</span>
            </div>
            <div className="hv-panel__body--none">
              <OperationsTable operations={operations} locale={locale} />
            </div>
          </section>

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('pp.shifts')}</span>
              <span className="hv-panel__aside">{t('pp.shifts.aside')}</span>
            </div>
            <div className="hv-panel__body">
              <OutputSpark series={output} />
              <div className="hv-row hv-row--between" style={{ marginTop: 'var(--hv-2)' }}>
                <span className="hv-micro">
                  {t('pp.shifts.average', { value: average.toFixed(1) })}
                </span>
                {best && best[1] > 0 && (
                  <span className="hv-micro">
                    {t('pp.shifts.best', { day: formatDay(best[0], locale), count: best[1] })}
                  </span>
                )}
              </div>
            </div>
          </section>
        </div>

        <div className="hv-stack">
          <Badges card={shift.find((one) => one.operator_id === actor?.user_id)} locale={locale} />

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('pp.people')}</span>
              <span className="hv-panel__aside">
                {t('pp.people.aside', { count: shift.length })}
              </span>
            </div>
            <div className="hv-panel__body--none">
              <ShiftPanel shift={shift} locale={locale} meId={actor?.user_id ?? null} />
            </div>
            <div className="hv-panel__foot">
              <span>{t('pp.people.formula')}</span>
              <span>{t('pp.people.trainee')}</span>
            </div>
          </section>

          <section className="hv-frame" style={{ '--ink': 'var(--hv-warn)' } as React.CSSProperties}>
            <span className="hv-h hv-warn">{t('pp.consumables')}</span>
            <Consumables consumables={consumables} locale={locale} />
          </section>
        </div>
      </section>

      {open && (
        <TaskDetail
          task={open}
          locale={locale}
          mayAdvance={mayAdvance}
          mayInspect={mayInspect}
          onChanged={() => void refetch()}
          onClose={() => setOpenId(null)}
        />
      )}
    </div>
  )
}

/**
 * The signed-in operator's own marks.
 *
 * Unearned badges are shown dimmed rather than hidden, so there is something
 * visible to earn — and the footer says plainly that none of them can be awarded
 * by hand, because a badge somebody can grant is a badge somebody can withhold.
 */
function Badges({
  card,
  locale,
}: {
  card: { badges: { code: string; tier: number; detail: Record<string, string> }[] } | undefined
  locale: Locale
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) =>
    translate(locale, key, details)
  const badges = card?.badges ?? []
  const earned = badges.filter((badge) => badge.tier > 0).length

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('pp.badges')}</span>
        <span className="hv-panel__aside">
          {t('pp.badges.aside', { earned, total: badges.length })}
        </span>
      </div>
      <div className="hv-panel__body">
        {badges.length === 0 ? (
          <p className="hv-hint">{t('pp.people.empty')}</p>
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
          {t('pp.badges.foot')}
        </p>
      </div>
    </section>
  )
}

/**
 * The mark inside a badge plate.
 *
 * Monochrome glyphs, never colour: colour means machine state everywhere in this
 * system and a badge is not a state.
 */
function glyph(code: string): string {
  if (code === 'badge.volume') return 'Σ'
  if (code === 'badge.no_returns') return 'Q'
  if (code === 'badge.pace') return '↑'
  return code.replace('badge.mastery.', '').slice(0, 2).toUpperCase()
}

/** The figures behind a badge, folded into the reader's own language. */
function caption(
  badge: { code: string; detail: Record<string, string> },
  locale: Locale,
): string {
  const t = (key: MessageKey, details?: Record<string, unknown>) =>
    translate(locale, key, details)
  if (badge.code === 'badge.pace') {
    return badge.detail.pace
      ? t('pp.badge.pace_value', { pace: `${Number(badge.detail.pace).toFixed(0)}%` })
      : t('pp.badge.unearned')
  }
  if (badge.code === 'badge.no_returns') {
    // Both halves, always. The caption used to say "20 БЕЗ ВОЗВРАТОВ" for an
    // operator with two returns — the badge was correctly unearned and its own
    // label contradicted the reason.
    return t('pp.badge.clean', {
      completed: badge.detail.completed ?? '0',
      returns: badge.detail.returns ?? '0',
    })
  }
  return t('pp.badge.progress', {
    completed: badge.detail.completed ?? '0',
    next: badge.detail.next ?? '—',
  })
}

/** Re-exported so the board's minute formatting has one definition. */
export { formatMinutes }

/** The gauge component, re-exported for tests that assert on it directly. */
export { Blocks }
