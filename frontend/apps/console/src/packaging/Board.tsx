import type { Locale, MessageKey } from '@printorian/ui'
import { translate } from '@printorian/ui'

import { Blocks } from '../postproduction/PostProductionPage'
import {
  formatClock,
  formatCountdown,
  formatCover,
  formatDims,
  formatGrams,
  formatMoney,
  formatPercent,
  stockTone,
} from './format'
import type { PackColumn, PackScore, Parcel, Pickup, TaraRow } from './types'

/**
 * The packing board: one column per state, ordered by the van.
 *
 * Everything in the 19:30 pickup is due at 19:30 whatever time it was inspected,
 * so the server orders by the cutoff and the client does not re-sort. The stripe
 * down each card comes from that same deadline and from nothing a person set —
 * a priority anyone can raise is one everyone raises.
 */
export function ParcelBoard({
  columns,
  locale,
  onOpen,
}: {
  columns: PackColumn[]
  locale: Locale
  onOpen: (parcel: Parcel) => void
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)

  if (columns.every((column) => column.tasks.length === 0)) {
    return <p className="hv-hint">{t('pk.board.empty')}</p>
  }

  return (
    <div className="hv-board">
      {columns.map((column) => (
        // Empty columns stay. A board whose columns appear and vanish cannot be
        // read by position, and position is how a packer scans it in passing.
        <div className={`hv-col${columnModifier(column.status)}`} key={column.status}>
          <div className="hv-col__h">
            <span>{t(`pk.status.${column.status}` as MessageKey)}</span>
            <span className="hv-col__n">{column.tasks.length}</span>
          </div>
          <div className="hv-col__b">
            {column.tasks.map((parcel) => (
              <button
                className="hv-task"
                type="button"
                key={parcel.id}
                data-pri={priority(parcel)}
                onClick={() => onOpen(parcel)}
              >
                <span className="hv-task__top">
                  <span>{parcel.number}</span>
                  <CutoffChip parcel={parcel} locale={locale} />
                </span>
                <span className="hv-task__t">
                  {parcel.order_number || '—'} · {parcel.lines[0]?.model_name ?? '—'}
                </span>
                <span className="hv-task__m">
                  <span>{t('pk.card.items', { count: parcel.items })}</span>
                  {/* Always how it leaves, even when the parcel is held. The chip
                      above already carries the reason, and printing it twice on
                      one card costs the slot that tells the packer whether this
                      is a courier order — which is what they need to know when
                      the hold is cleared at 19:10. */}
                  <span>
                    {translate(
                      locale,
                      `pk.method.${parcel.delivery_method}` as MessageKey,
                    ).toUpperCase()}
                  </span>
                  <span>
                    {parcel.status === 'packing'
                      ? t('pk.card.step', {
                          position: nextStep(parcel),
                          total: parcel.steps.length,
                        })
                      : formatGrams(parcel.estimated_grams, locale)}
                  </span>
                </span>
                {parcel.status === 'packing' && parcel.steps.length > 0 && (
                  <span className="hv-meter hv-meter--thin" style={{ marginTop: 'var(--hv-1)' }}>
                    <span className="hv-meter__fill" style={{ width: `${progressOf(parcel)}%` }} />
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
 * A held parcel shows what is blocking it rather than when the van comes: the
 * packer cannot act on the deadline, and a red countdown on work somebody else
 * has to unblock is noise pointed at the wrong person.
 */
function CutoffChip({ parcel, locale }: { parcel: Parcel; locale: Locale }) {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)

  if (parcel.status === 'held') {
    return (
      <span className="hv-due" data-state="soon">
        {holdLabel(parcel, locale)}
      </span>
    )
  }
  if (parcel.status === 'ready') {
    return <span className="hv-due">{formatClock(parcel.finished_at, locale)}</span>
  }
  if (parcel.status === 'packing') {
    return (
      <span className="hv-due" data-state="ok">
        {t('pk.card.of_norm', {
          elapsed: formatCountdown(parcel.elapsed_minutes, locale),
          norm: formatCountdown(parcel.norm_minutes, locale),
        })}
      </span>
    )
  }
  if (parcel.minutes_to_cutoff === null) {
    return (
      <span className="hv-due" data-state="ok">
        {t('pk.card.no_van')}
      </span>
    )
  }

  const key =
    parcel.urgency === 'late'
      ? 'pk.card.cutoff_missed'
      : parcel.urgency === 'soon'
        ? 'pk.card.cutoff_soon'
        : 'pk.card.cutoff_at'
  return (
    <span className="hv-due" data-state={parcel.urgency}>
      {t(key, {
        time:
          parcel.urgency === 'ok'
            ? formatClock(parcel.cutoff_at, locale)
            : formatCountdown(parcel.minutes_to_cutoff, locale),
      })}
    </span>
  )
}

function holdLabel(parcel: Parcel, locale: Locale): string {
  if (parcel.hold_reason === null) return translate(locale, 'pk.status.held')
  return translate(locale, `pk.hold.${parcel.hold_reason}` as MessageKey).toUpperCase()
}

/**
 * The stripe down the left of a card.
 *
 * Work in hand is `live` whatever its deadline: the packer is already on it, and
 * a red bar on the one parcel actively being dealt with points at nothing.
 */
function priority(parcel: Parcel): string {
  if (parcel.status === 'packing') return 'live'
  if (parcel.status === 'held' || parcel.urgency === 'late') return 'rush'
  if (parcel.urgency === 'soon') return 'soon'
  return 'normal'
}

function columnModifier(status: string): string {
  if (status === 'packing') return ' hv-col--wip'
  if (status === 'ready') return ' hv-col--done'
  return ''
}

function nextStep(parcel: Parcel): number {
  return parcel.steps.filter((step) => step.done_at !== null).length + 1
}

function progressOf(parcel: Parcel): number {
  const done = parcel.steps.filter((step) => step.done_at !== null).length
  return parcel.steps.length === 0 ? 0 : Math.round((done / parcel.steps.length) * 100)
}

/**
 * What the bench draws from, and how long it lasts.
 *
 * The cover column is computed from what the post actually consumed rather than
 * from a rate somebody typed in, which is why a box nobody has used shows a dash
 * and not an infinity: it has no measured rate, and pretending otherwise is how
 * a shelf runs out on a Friday.
 */
export function TaraTable({ tara, locale }: { tara: TaraRow[]; locale: Locale }) {
  const t = (key: MessageKey) => translate(locale, key)

  if (tara.length === 0) return <p className="hv-hint">{t('pk.tara.empty')}</p>

  return (
    <table className="hv-table">
      <thead>
        <tr>
          <th>{t('pk.tara.kind')}</th>
          <th>{t('pk.tara.size')}</th>
          <th data-align="end">{t('pk.tara.price')}</th>
          <th data-align="end">{t('pk.tara.rate')}</th>
          <th data-align="end">{t('pk.tara.stock')}</th>
          <th>{t('pk.tara.cover')}</th>
        </tr>
      </thead>
      <tbody>
        {tara.map((row) => {
          const tone = stockTone(row.stock, row.reorder_at)
          const mark = tone === 'bad' ? 'hv-bad' : tone === 'warn' ? 'hv-warn' : undefined
          return (
            <tr key={row.id}>
              <td>{row.name}</td>
              <td>
                {row.inner_length_mm && row.inner_width_mm && row.inner_height_mm
                  ? formatDims(row.inner_length_mm, row.inner_width_mm, row.inner_height_mm, locale)
                  : translate(locale, `pk.unit.${row.unit}` as MessageKey)}
              </td>
              <td data-align="end">{formatMoney(row.price, locale)}</td>
              <td data-align="end">{Number(row.used_per_month).toFixed(0)}</td>
              <td data-align="end" className={mark}>
                {Number(row.stock).toFixed(0)}
              </td>
              <td className={mark}>{formatCover(row.months_left, locale)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** Who is on the bench, and how their shift is going. */
export function ShiftPanel({
  shift,
  locale,
  meId,
}: {
  shift: PackScore[]
  locale: Locale
  meId: string | null
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)

  if (shift.length === 0) return <p className="hv-hint">{t('pk.people.empty')}</p>

  return (
    <>
      {shift.map((card) => (
        <div className="hv-score" key={card.operator_id}>
          <span className="hv-avatar hv-avatar--sm">{initials(card.operator_name)}</span>
          <span>
            <span className="hv-score__n">
              {card.operator_name || '—'}
              {card.operator_id === meId && <span className="hv-micro"> {t('pk.people.you')}</span>}
            </span>
            <span className="hv-score__m">
              {t('pk.people.line', {
                packed: card.packed,
                minutes:
                  card.average_minutes === null
                    ? '—'
                    : formatCountdown(card.average_minutes, locale),
                short: card.discrepancies,
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
 * so the local part is used, or every packer would be "A@".
 */
function initials(name: string): string {
  const local = name.split('@')[0] ?? ''
  const parts = local.split(/[\s._-]+/).filter(Boolean)
  if (parts.length === 0) return '—'
  if (parts.length === 1) return (parts[0] as string).slice(0, 2).toUpperCase()
  return `${(parts[0] as string)[0]}${(parts[1] as string)[0]}`.toUpperCase()
}

/** What is going out on each van today, and how much of it there is. */
export function PickupList({ pickups, locale }: { pickups: Pickup[]; locale: Locale }) {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)

  if (pickups.length === 0) return <p className="hv-hint">{t('pk.pickups.empty')}</p>

  return (
    <ul className="hv-leaders" style={{ marginTop: 'var(--hv-2)' }}>
      {pickups.map((pickup) => (
        <li className="hv-leader" key={`${pickup.method}-${pickup.carrier_code}-${pickup.at}`}>
          <span className="hv-leader__k">
            {pickup.carrier_code || translate(locale, `pk.method.${pickup.method}` as MessageKey)} ·{' '}
            {formatClock(pickup.at, locale)}
          </span>
          <span className="hv-leader__fill" />
          <span className="hv-leader__v">{t('pk.pickups.count', { count: pickup.parcels })}</span>
        </li>
      ))}
    </ul>
  )
}

/**
 * One row of the thirty-day panel.
 *
 * `value` is already rendered by the caller, dash included — the decision about
 * what an unmeasured figure looks like belongs in `format.ts` and is made once.
 */
export function Metric({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'good' | 'warn' | 'bad'
}) {
  return (
    <li className="hv-leader" {...(tone ? { 'data-tone': tone } : {})}>
      <span className="hv-leader__k">{label}</span>
      <span className="hv-leader__fill" />
      <span className="hv-leader__v">{value}</span>
    </li>
  )
}

export { formatPercent }
