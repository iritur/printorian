import type { Locale, MessageKey } from '@printorian/ui'
import { translate } from '@printorian/ui'

import { formatClock, formatMinutes, paceTone } from './format'
import type { Board, Column, Consumable, OperationStat, Scorecard, Task } from './types'

/**
 * The task board: one column per status, ordered by the promise.
 *
 * A queue sorted by when work landed ships the wrong order first on every busy
 * day, so the server orders by the customer's deadline and the client does not
 * re-sort. Each card carries a countdown against that promise and a priority
 * stripe derived from it — never a priority somebody set by hand, because a
 * priority anyone can set is one everyone sets.
 */
export function TaskBoard({
  columns,
  locale,
  onOpen,
}: {
  columns: Column[]
  locale: Locale
  onOpen: (task: Task) => void
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) =>
    translate(locale, key, details)

  if (columns.every((column) => column.tasks.length === 0)) {
    return <p className="hv-hint">{t('pp.board.empty')}</p>
  }

  return (
    <div className="hv-board">
      {columns.map((column) => (
        <div
          className={`hv-col${columnModifier(column.status)}`}
          key={column.status}
          // Empty columns stay: a board whose columns appear and vanish as work
          // moves cannot be scanned by position, and position is how an operator
          // reads it from two metres away.
        >
          <div className="hv-col__h">
            <span>{t(`pp.status.${column.status}` as MessageKey)}</span>
            <span className="hv-col__n">{column.tasks.length}</span>
          </div>
          <div className="hv-col__b">
            {column.tasks.map((task) => (
              <button
                className="hv-task"
                type="button"
                key={task.id}
                data-pri={priority(task)}
                onClick={() => onOpen(task)}
              >
                <span className="hv-task__top">
                  <span>{task.number}</span>
                  <DueChip task={task} locale={locale} />
                </span>
                <span className="hv-task__t">
                  {t(`pp.kind.${task.kind}` as MessageKey)} · {task.model_name || '—'}
                </span>
                <span className="hv-task__m">
                  <span>{task.order_number || '—'}</span>
                  <span>{t('pp.card.units', { count: task.quantity })}</span>
                  <span>
                    {task.status === 'in_progress'
                      ? t('pp.card.step', {
                          position: nextStep(task),
                          total: task.steps.length,
                        })
                      : t('pp.card.norm', {
                          norm: formatMinutes(task.norm_minutes, locale),
                        })}
                  </span>
                </span>
                {task.status === 'in_progress' && task.steps.length > 0 && (
                  <span className="hv-meter hv-meter--thin" style={{ marginTop: 'var(--hv-1)' }}>
                    <span
                      className="hv-meter__fill"
                      style={{ width: `${progressOf(task)}%` }}
                    />
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

/**
 * The countdown, or what the card says instead of one.
 *
 * A drying batch shows when it will be ready rather than when the order is due:
 * the operator cannot act on it before then, and showing the promise would put a
 * red chip on work nobody is allowed to touch.
 */
function DueChip({ task, locale }: { task: Task; locale: Locale }) {
  const t = (key: MessageKey, details?: Record<string, unknown>) =>
    translate(locale, key, details)

  if (task.status === 'curing' && task.cure_until) {
    return (
      <span className="hv-due" data-state="soon">
        {t('pp.card.cure_until', { time: formatClock(task.cure_until, locale) })}
      </span>
    )
  }
  if (task.status === 'returned') {
    return (
      <span className="hv-due" data-state="late">
        {t('pp.card.repeat')}
      </span>
    )
  }
  if (task.status === 'in_progress') {
    return (
      <span className="hv-due" data-state="ok">
        {t('pp.card.of_norm', {
          elapsed: formatMinutes(task.elapsed_minutes, locale),
          norm: formatMinutes(task.norm_minutes, locale),
        })}
      </span>
    )
  }
  if (task.minutes_to_due === null) {
    return (
      <span className="hv-due" data-state="ok">
        {t('pp.card.due_none')}
      </span>
    )
  }

  const minutes = Number(task.minutes_to_due)
  const key =
    task.urgency === 'late'
      ? 'pp.card.due_late'
      : task.urgency === 'soon'
        ? 'pp.card.due_soon'
        : 'pp.card.due_ok'
  return (
    <span className="hv-due" data-state={task.urgency}>
      {t(key, {
        time:
          task.urgency === 'ok'
            ? formatClock(task.due_at as string, locale)
            : formatMinutes(String(Math.abs(minutes)), locale),
      })}
    </span>
  )
}

/**
 * The stripe down the left of a card.
 *
 * Work in hand is `live` whatever its deadline — the operator is already on it,
 * and colouring it by urgency would put a red bar on the one task that is
 * actively being dealt with.
 */
function priority(task: Task): string {
  if (task.status === 'in_progress') return 'live'
  if (task.status === 'returned' || task.urgency === 'late') return 'rush'
  if (task.urgency === 'soon') return 'soon'
  return 'normal'
}

function columnModifier(status: string): string {
  if (status === 'in_progress') return ' hv-col--wip'
  if (status === 'for_qc') return ' hv-col--done'
  return ''
}

function nextStep(task: Task): number {
  return task.steps.filter((step) => step.done_at !== null).length + 1
}

function progressOf(task: Task): number {
  const done = task.steps.filter((step) => step.done_at !== null).length
  return task.steps.length === 0 ? 0 : Math.round((done / task.steps.length) * 100)
}

/**
 * Fact against norm, per operation.
 *
 * The table that tells the farm its norms are wrong. It deliberately does not
 * say whether painting running under norm is a training problem or a norm
 * problem — that is a conversation, not a computation.
 */
export function OperationsTable({
  operations,
  locale,
}: {
  operations: OperationStat[]
  locale: Locale
}) {
  const t = (key: MessageKey) => translate(locale, key)

  if (operations.length === 0) return <p className="hv-hint">{t('pp.ops.empty')}</p>

  return (
    <table className="hv-table">
      <thead>
        <tr>
          <th>{t('pp.ops.operation')}</th>
          <th data-align="end">{t('pp.ops.completed')}</th>
          <th data-align="end">{t('pp.ops.norm')}</th>
          <th data-align="end">{t('pp.ops.actual')}</th>
          <th>{t('pp.ops.pace')}</th>
          <th data-align="end">{t('pp.ops.returns')}</th>
        </tr>
      </thead>
      <tbody>
        {operations.map((row) => (
          <tr key={row.kind}>
            <td>{translate(locale, `pp.kind.${row.kind}` as MessageKey)}</td>
            <td data-align="end">{row.completed}</td>
            <td data-align="end">{formatMinutes(row.norm_minutes, locale)}</td>
            <td data-align="end">{formatMinutes(row.actual_minutes, locale)}</td>
            <td>
              <Blocks value={row.pace_percent} />
            </td>
            <td data-align="end" className={row.returns > 0 ? 'hv-warn' : undefined}>
              {row.returns}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * A ten-block gauge.
 *
 * Segmented rather than a bar, because a score of 7 should be countable rather
 * than estimated off a length — and because nothing in this system is round.
 */
export function Blocks({ value, max = 120 }: { value: string | null; max?: number }) {
  if (value === null) return <span className="hv-micro">—</span>
  const numeric = Number(value)
  const filled = Math.max(0, Math.min(10, Math.round((numeric / max) * 10)))
  return (
    <span className="hv-blocks" data-tone={paceTone(numeric)}>
      {/* Ten fixed positions, so the index *is* the identity — these blocks are
          a ruler, not a list of things. */}
      {Array.from({ length: 10 }, (_, index) => (
        <i key={`block-${index}`} {...(index < filled ? { 'data-on': true } : {})} />
      ))}
      <b>{numeric.toFixed(numeric >= 100 ? 0 : 1)}</b>
    </span>
  )
}

/** The shop, over thirty days. Every figure is a recorded fact. */
export function ShiftPanel({
  shift,
  locale,
  meId,
}: {
  shift: Scorecard[]
  locale: Locale
  meId: string | null
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) =>
    translate(locale, key, details)

  if (shift.length === 0) return <p className="hv-hint">{t('pp.people.empty')}</p>

  return (
    <>
      {shift.map((card) => (
        <div className="hv-score" key={card.operator_id}>
          <span className="hv-avatar hv-avatar--sm">{initials(card.operator_name)}</span>
          <span>
            <span className="hv-score__n">
              {card.operator_name || '—'}
              {card.operator_id === meId && <span className="hv-micro"> {t('pp.people.you')}</span>}
            </span>
            <span className="hv-score__m">
              {card.pace_percent === null
                ? t('pp.people.no_pace', {
                    completed: card.completed,
                    returns: card.returns,
                  })
                : t('pp.people.line', {
                    completed: card.completed,
                    pace: `${Number(card.pace_percent).toFixed(0)}%`,
                    returns: card.returns,
                  })}
            </span>
          </span>
          <Blocks value={card.score} max={10} />
        </div>
      ))}
    </>
  )
}

/**
 * Two letters, for the avatar plate.
 *
 * From the display name the farm chose, which for a seeded account is an email —
 * so the local part is used rather than the whole address, or every operator
 * would be "A@".
 */
function initials(name: string): string {
  const local = name.split('@')[0] ?? ''
  const parts = local.split(/[\s._-]+/).filter(Boolean)
  if (parts.length === 0) return '—'
  if (parts.length === 1) return (parts[0] as string).slice(0, 2).toUpperCase()
  return `${(parts[0] as string)[0]}${(parts[1] as string)[0]}`.toUpperCase()
}

/** What the post is running out of. */
export function Consumables({
  consumables,
  locale,
}: {
  consumables: Consumable[]
  locale: Locale
}) {
  const t = (key: MessageKey) => translate(locale, key)

  if (consumables.length === 0) return <p className="hv-hint">{t('pp.consumables.empty')}</p>

  return (
    <ul className="hv-leaders" style={{ marginTop: 'var(--hv-2)' }}>
      {consumables.map((item) => {
        const remaining = Number(item.remaining)
        const threshold = Number(item.reorder_at)
        const tone = remaining <= 0 ? 'bad' : threshold > 0 && remaining <= threshold ? 'warn' : ''
        return (
          <li className="hv-leader" key={item.id} {...(tone ? { 'data-tone': tone } : {})}>
            <span className="hv-leader__k">{item.name}</span>
            <span className="hv-leader__fill" />
            <span className="hv-leader__v">
              {remaining <= 0
                ? t('pp.unit.out')
                : `${remaining} ${translate(locale, `pp.unit.${item.unit}` as MessageKey)}`}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

/** Completed tasks per day. Quiet days are zeroes, so a dead week reads as one. */
export function OutputSpark({ series }: { series: Board['output_by_day'] }) {
  const values = series.map(([, count]) => count)
  const peak = Math.max(...values, 1)
  const step = values.length > 1 ? 600 / (values.length - 1) : 600
  const points = values.map(
    (value, index) => `${Math.round(index * step)},${Math.round(60 - (value / peak) * 54)}`,
  )

  return (
    <svg className="hv-spark" viewBox="0 0 600 60" preserveAspectRatio="none" role="presentation">
      <polygon points={`0,60 ${points.join(' ')} 600,60`} />
      <polyline points={points.join(' ')} />
    </svg>
  )
}
