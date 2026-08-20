import type { ReactNode } from 'react'

import type { Locale } from '@printorian/ui'

import { changeLabel, directionOf, toneOf } from './format'
import type { Polarity } from './format'
import type { Trend } from './types'

/**
 * One KPI tile.
 *
 * The kit's claim is that a number worth putting on a dashboard always has three
 * parts: the value, what it is measured against, and which way it is moving. The
 * component makes all three positional so a tile cannot be built without them —
 * `note` is the basis, `foot` is the pair of secondary figures, and the delta
 * comes from the trend rather than being passed in as decoration.
 */
export function Kpi({
  label,
  value,
  unit,
  trend,
  polarity,
  note,
  foot,
  tone,
  compact = false,
  locale,
}: {
  label: string
  value: string
  unit?: string | undefined
  /** Omitted for a figure with no meaningful history, such as "in progress now". */
  trend?: Trend | undefined
  polarity?: Polarity | undefined
  note?: ReactNode | undefined
  foot?: [ReactNode, ReactNode] | undefined
  tone?: 'live' | 'good' | 'warn' | 'bad' | undefined
  /**
   * A denser tile, for a row that is context rather than the screen's subject.
   *
   * The dashboard's orders row is read *after* the status wall — it answers "how
   * is trade", not "what is the farm doing right now" — and at full size it
   * pushed the wall off the first screen entirely.
   */
  compact?: boolean | undefined
  locale: Locale
}) {
  const change = trend ? changeLabel(trend, locale) : null

  return (
    <div
      className={compact ? 'hv-frame hv-kpi hv-kpi--compact' : 'hv-frame hv-kpi'}
      {...(tone ? { 'data-tone': tone } : {})}
    >
      <span className="hv-label">{label}</span>
      <span className="hv-kpi__v">
        {value}
        {unit && <small>{unit}</small>}
        {change && trend && (
          <span
            className="hv-kpi__d"
            data-dir={directionOf(trend)}
            data-sentiment={toneOf(trend, polarity ?? 'more_is_better')}
          >
            {change}
          </span>
        )}
      </span>
      {note && <span className="hv-micro">{note}</span>}
      {foot && (
        <span className="hv-kpi__foot">
          <span>{foot[0]}</span>
          <span>{foot[1]}</span>
        </span>
      )}
    </div>
  )
}
