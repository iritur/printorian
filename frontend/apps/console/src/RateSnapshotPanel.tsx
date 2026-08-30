import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, translate, translateError } from '@printorian/ui'

/**
 * «Тарифы заказа» — the rates this order's price was actually built from.
 *
 * ADR-0020 has always held: a rate edit changes the next quote and nothing
 * already sold. What the console could not do is *show* it. The settings screen
 * lets an owner change seventeen pricing rates, audited «было · стало»; a
 * customer asks why a repeat order costs more than last month's; the system has
 * both snapshots and could display neither. The audit log answers what changed
 * and when — this answers what *this order* was priced against, which is the
 * question being asked.
 *
 * **The hash is shown, and it is the useful part.** The snapshot id is a content
 * hash, so two orders carrying the same id were priced from identical rates and
 * the difference between them is in the configuration, not the price book. That
 * comparison is a glance rather than an investigation.
 *
 * **A rate is rendered by its key, with the label the settings screen already
 * owns.** The backend sends keys and numbers (ADR-0012), and
 * `settings.field.pricing.*` already carries a translation for each; a key with
 * no entry falls back to the key itself rather than to a blank, because a rate
 * nobody has named is still a number somebody was charged.
 */

interface Snapshot {
  id: string
  engine_version: string
  payload: Record<string, unknown>
  created_at: string
}

/** Keys that describe the payload rather than being rates in it. */
const META = new Set(['schema_version', 'snapshot_id'])

/**
 * One stored rate as text.
 *
 * `discounts` is a list of `{min_quantity, percent}` and `currency` is a bare
 * code, so a panel that did `String(value)` over every rate would render
 * `[object Object]` for the volume ladder — the one rate an operator is most
 * likely to be checking.
 */
function show(value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .map((tier) => {
        const rung = tier as { min_quantity?: number; percent?: string }
        return `${rung.min_quantity ?? '?'}+ → ${rung.percent ?? '?'}%`
      })
      .join(', ')
  }
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '✓' : '—'
  return String(value)
}

export function RateSnapshotPanel({
  order,
  locale,
}: {
  order: { id: string; rate_snapshot_id: string | null }
  locale: Locale
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [error, setError] = useState<string | null>(null)

  const recorded = order.rate_snapshot_id !== null

  const load = useCallback(async () => {
    // An order that pinned nothing is a fact the table already carries, so the
    // request is not made at all — asking would produce a 404 that means "there
    // is nothing to show", which is not an error worth rendering as one.
    if (!recorded) return
    try {
      setSnapshot(await api.get<Snapshot>(`/orders/${order.id}/rate-snapshot`))
      setError(null)
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
    }
  }, [order.id, recorded, locale])

  useEffect(() => {
    void (async () => {
      await load()
    })()
  }, [load])

  // "Its rates were never recorded" and "they have not loaded yet" are different
  // facts, and neither of them is a table of zeros (ADR-0007).
  if (!recorded) {
    return (
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('desk.snapshot')}</span>
        </div>
        <div className="hv-panel__body">
          <p className="hv-faint">{t('desk.snapshot.none')}</p>
        </div>
      </section>
    )
  }
  if (error) return <p className="hv-faint">{error}</p>
  if (snapshot === null) return null

  const rates = Object.entries(snapshot.payload).filter(([key]) => !META.has(key))

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('desk.snapshot')}</span>
        {/* The hash, abbreviated. Two orders showing the same one were priced
            from identical rates — which is the comparison this panel is for. */}
        <span className="hv-panel__aside" title={snapshot.id}>
          {snapshot.id.slice(0, 12)} · {snapshot.engine_version}
        </span>
      </div>
      <div className="hv-panel__body--none">
        <div className="hv-table-wrap">
          <table className="hv-table">
            <thead>
              <tr>
                <th>{t('desk.snapshot.rate')}</th>
                <th data-align="end">{t('desk.snapshot.value')}</th>
              </tr>
            </thead>
            <tbody>
              {rates.map(([key, value]) => (
                <tr key={key}>
                  <td>{translate(locale, `settings.field.pricing.${key}` as MessageKey) || key}</td>
                  <td data-align="end">{show(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
