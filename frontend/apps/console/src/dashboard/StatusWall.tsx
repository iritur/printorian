import type { Locale, MessageKey } from '@printorian/ui'
import { translate } from '@printorian/ui'

import type { Alert, WallNode, Zone } from './types'
import { formatGrams, formatTime } from './format'

/**
 * The status wall: one glowing square per machine, grouped by shop zone.
 *
 * Twelve rows of text cannot be read from across the room and twelve coloured
 * squares can, which is the whole argument for this shape over a table. Position
 * still answers *where*, so the grouping is the printer's recorded location and
 * the order inside a zone is the server's — a wall whose squares moved when a
 * machine changed state would be unreadable exactly when it matters.
 *
 * Each square is a real `<button>`. The wall is the primary way into a machine
 * from this screen, and a clickable `<div>` would put every printer behind a
 * mouse.
 */
export function StatusWall({
  zones,
  locale,
  onOpen,
}: {
  zones: Zone[]
  locale: Locale
  onOpen: (node: WallNode) => void
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) =>
    translate(locale, key, details)

  if (zones.length === 0) {
    return <p className="hv-hint">{t('dashboard.fleet.empty')}</p>
  }

  return (
    <>
      {zones.map((zone) => (
        <div className="hv-zone" key={zone.name || '·'}>
          <div className="hv-zone__head">
            <span>
              {zone.name
                ? t('dashboard.fleet.zone', { name: zone.name, count: zone.nodes.length })
                : t('dashboard.fleet.unzoned')}
            </span>
            <span>{t('dashboard.fleet.load', { percent: zone.load_percent })}</span>
          </div>
          <div className="hv-matrix">
            {zone.nodes.map((node) => (
              <button
                className="hv-node"
                type="button"
                key={node.id}
                data-state={node.state}
                onClick={() => onOpen(node)}
                aria-label={`${node.name} · ${translate(
                  locale,
                  `printer.state.${node.state}` as MessageKey,
                )}`}
              >
                <span className="hv-node__id">{node.name}</span>
                <span className="hv-node__pct">{caption(node, locale)}</span>
                {node.progress_percent !== null && (
                  <span
                    className="hv-node__fill"
                    style={{ '--p': `${node.progress_percent}%` } as React.CSSProperties}
                  />
                )}
              </button>
            ))}
          </div>
        </div>
      ))}
    </>
  )
}

/**
 * The one line inside a square.
 *
 * A percentage when the machine is running, its state otherwise. Progress on an
 * idle machine would be the leftover from the last job, which reads as work in
 * hand — the exact misreading the wall exists to prevent.
 */
function caption(node: WallNode, locale: Locale): string {
  if (node.state === 'printing' && node.progress_percent !== null) {
    return `${node.progress_percent}%`
  }
  return translate(locale, `printer.state.${node.state}` as MessageKey)
}

/**
 * The attention feed.
 *
 * Every row is derived from live data rather than stored, so there is nothing to
 * acknowledge and nothing to forget to close: a machine that came back stops
 * being an alert because it is online.
 */
export function AlertFeed({
  alerts,
  locale,
  onOpen,
}: {
  alerts: Alert[]
  locale: Locale
  onOpen: (printerId: string) => void
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) =>
    translate(locale, key, details)

  if (alerts.length === 0) {
    return (
      <div className="hv-panel__body">
        <p className="hv-hint">{t('dashboard.alerts.none')}</p>
      </div>
    )
  }

  return (
    <div className="hv-panel__body--none">
      {alerts.map((alert, index) => (
        <div className="hv-alert" data-tone={alert.tone} key={`${alert.code}-${alert.subject}-${index}`}>
          <span className="hv-alert__t">
            {alert.at ? formatTime(alert.at, locale) : '—'}
          </span>
          <span className="hv-alert__b">
            <b>{translate(locale, alert.code as MessageKey, { subject: alert.subject })}</b>
            <em>{detail(alert, locale)}</em>
          </span>
          {alert.subject_id && (
            <button
              className="hv-btn hv-btn--sm"
              type="button"
              onClick={() => onOpen(alert.subject_id as string)}
            >
              {t('dashboard.alert.open')}
            </button>
          )}
        </div>
      ))}
    </div>
  )
}

/**
 * The second line of an alert, built from the structured facts the backend sent.
 *
 * The API emits fields, never sentences (ADR-0012), so the grams and the state
 * arrive as data and this is where they become prose in the reader's language.
 */
function detail(alert: Alert, locale: Locale): string {
  if (alert.detail.short_grams) {
    return translate(locale, 'dashboard.alert.short', {
      grams: formatGrams(alert.detail.short_grams, locale),
      committed: formatGrams(alert.detail.committed_grams ?? '0', locale),
      held: formatGrams(alert.detail.held_grams ?? '0', locale),
    })
  }
  if (alert.detail.state) {
    return translate(locale, `printer.state.${alert.detail.state}` as MessageKey)
  }
  return ''
}
