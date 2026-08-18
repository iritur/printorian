import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useLiveEvents } from '@printorian/events'
import type { LiveEvent } from '@printorian/events'
import { DataTable, api, translate, useSession } from '@printorian/ui'
import type { Column, Locale, MessageKey, StatusTag } from '@printorian/ui'

import { PrinterDetail, PrinterForm } from './FleetAdmin'

/**
 * The fleet table, live.
 *
 * ## Why events refetch instead of patching rows
 *
 * `fleet.printer_state_changed` carries the new state and nothing else — not
 * progress, not the ETA, not whether the machine now needs attention. Patching
 * the row from the event would leave those fields showing values from before the
 * change, and a client that recomputed them would be reimplementing fleet
 * policy in TypeScript, where it would drift from the Python that owns it.
 *
 * So an event means "you are out of date", not "here is the new truth". The
 * server stays the single source of that. Bursts are coalesced, because a farm
 * restarting produces a dozen events in a second and that is one refetch.
 */

const VIEW_PRODUCTION = 'view_production'
const MANAGE_FLEET = 'manage_fleet'
/** Long enough to absorb a burst, short enough to feel immediate. */
const COALESCE_MS = 250

export interface ServiceOperation {
  id: string
  kind: string
  interval_hours: number
  is_due: boolean
  last_done_at: string | null
}

export interface PrinterRow {
  id: string
  name: string
  brand: string
  model: string
  serial: string
  connection_mode: string
  host: string | null
  access_code_set: boolean
  state: string
  location: string | null
  last_seen_at: string | null
  progress_percent: number | null
  eta: string | null
  current_job: string | null
  needs_attention: boolean
  maintenance_due: boolean
  printed_hours: string
  amortization_per_hour: string
  services: ServiceOperation[]
}

interface PrinterTable {
  rows: PrinterRow[]
  counts: { state: string; count: number }[]
  total: number
  attention: number
}

export function FleetPage({ locale }: { locale: Locale }) {
  const { actor, ready } = useSession()
  const t = useCallback((key: MessageKey) => translate(locale, key), [locale])

  const [table, setTable] = useState<PrinterTable | null>(null)
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(false)
  const [selected, setSelected] = useState<PrinterRow | null>(null)
  const entitled = actor?.permissions.includes(VIEW_PRODUCTION) ?? false
  const mayManage = actor?.permissions.includes(MANAGE_FLEET) ?? false

  const refetch = useCallback(async () => {
    try {
      setTable(await api.get<PrinterTable>('/printers'))
    } catch (exc: unknown) {
      // Keep the last known table on screen rather than blanking it; the status
      // badge already says the view may be stale.
      console.warn('fleet refresh failed', exc)
    } finally {
      setLoading(false)
    }
  }, [])

  // One pending refetch at a time, so a burst of events is one request.
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
      if (event.name.startsWith('fleet.')) invalidate()
    },
    [invalidate],
  )

  const status = useLiveEvents({
    onEvent,
    // Fires on first connect and after every gap — this is the initial load too.
    onResync: () => void refetch(),
    enabled: ready && entitled,
  })

  const columns = useMemo<Column<PrinterRow>[]>(
    () => [
      {
        key: 'name',
        header: t('fleet.column.printer'),
        value: (row) => row.name,
        render: (row) => (
          <span className="fleet__name">
            <strong>{row.name}</strong>
            {row.needs_attention && (
              <span className="fleet__flag" title={t('fleet.attention')} aria-label={t('fleet.attention')}>
                !
              </span>
            )}
            {row.maintenance_due && (
              <span className="fleet__flag" title={t('fleet.maintenance_due')} aria-label={t('fleet.maintenance_due')}>
                ⚙
              </span>
            )}
          </span>
        ),
      },
      {
        key: 'state',
        header: t('fleet.column.state'),
        value: (row) => row.state,
        render: (row) => translate(locale, `printer.state.${row.state}` as MessageKey),
      },
      {
        key: 'job',
        header: t('fleet.column.job'),
        value: (row) => row.current_job,
        render: (row) => row.current_job ?? t('common.none'),
      },
      {
        key: 'progress',
        header: t('fleet.column.progress'),
        align: 'end',
        value: (row) => row.progress_percent,
        render: (row) =>
          row.progress_percent === null ? t('common.none') : `${row.progress_percent}%`,
      },
      {
        key: 'eta',
        header: t('fleet.column.eta'),
        // Sort on the instant, display the wall clock.
        value: (row) => row.eta,
        render: (row) => (row.eta ? formatTime(row.eta, locale) : t('common.none')),
      },
      {
        key: 'last_seen',
        header: t('fleet.column.last_seen'),
        value: (row) => row.last_seen_at,
        render: (row) =>
          row.last_seen_at ? formatTime(row.last_seen_at, locale) : t('fleet.never'),
      },
      {
        key: 'location',
        header: t('fleet.column.location'),
        value: (row) => row.location,
        render: (row) => row.location ?? t('common.none'),
      },
      {
        // Already served by `PrinterView.printed_hours`; the kit's fleet table
        // has always had this column and the app simply never rendered it. It
        // is what the service card's intervals count against, so an operator
        // reading "осталось 82 ч" has the running total in the same row.
        key: 'printed_hours',
        header: t('fleet.column.hours'),
        // Sort numerically. `printed_hours` is a decimal *string* — money and
        // hours stay exact on the wire — so sorting it as text would put 9 after
        // 1000.
        value: (row) => Number(row.printed_hours),
        render: (row) => formatHours(row.printed_hours, locale),
        align: 'end',
      },
    ],
    [locale, t],
  )

  const statusTags = useMemo<StatusTag<PrinterRow>[]>(() => {
    const states = table?.counts.map((entry) => entry.state) ?? []
    return [
      {
        key: 'attention',
        label: t('fleet.attention'),
        match: (row) => row.needs_attention,
        tone: 'bad' as const,
      },
      ...states.map((state) => ({
        key: state,
        label: translate(locale, `printer.state.${state}` as MessageKey),
        match: (row: PrinterRow) => row.state === state,
        tone: toneFor(state),
      })),
    ]
  }, [table, locale, t])

  if (ready && !entitled) {
    return <p className="notice">{t('fleet.forbidden')}</p>
  }

  return (
    <section className="fleet">
      <header className="fleet__header">
        <h2>{t('fleet.title')}</h2>
        <div className="fleet__head-right">
          <ConnectionBadge status={status} locale={locale} />
          {mayManage && !adding && (
            <button type="button" onClick={() => setAdding(true)}>
              {t('fleet.add')}
            </button>
          )}
        </div>
      </header>

      {adding && (
        <PrinterForm
          locale={locale}
          onCancel={() => setAdding(false)}
          onDone={(created) => {
            setAdding(false)
            setSelected(created)
            void refetch()
          }}
        />
      )}

      <DataTable
        rows={table?.rows ?? []}
        columns={columns}
        rowKey={(row) => row.id}
        statusTags={statusTags}
        caption={t('fleet.title')}
        emptyLabel={t('common.empty')}
        isLoading={loading}
        loadingLabel={t('common.loading')}
        initialSort={{ key: 'name', direction: 'asc' }}
        onRowActivate={setSelected}
      />

      {selected && (
        <PrinterDetail
          // Re-read from the freshly fetched table so the panel follows live
          // telemetry instead of freezing at whatever the row said when clicked.
          printer={table?.rows.find((row) => row.id === selected.id) ?? selected}
          locale={locale}
          mayManage={mayManage}
          onClose={() => setSelected(null)}
          onChanged={(updated) => {
            setSelected(updated)
            void refetch()
          }}
        />
      )}
    </section>
  )
}

/**
 * Says whether the table can be trusted right now.
 *
 * A disconnected stream showing yesterday's states with no indication is how an
 * operator walks to a printer that finished an hour ago. `role="status"` so a
 * screen reader hears it change without the focus moving.
 */
function ConnectionBadge({ status, locale }: { status: string; locale: Locale }) {
  const live = status === 'live'
  const label =
    status === 'denied'
      ? translate(locale, 'fleet.denied')
      : live
        ? translate(locale, 'fleet.live')
        : translate(locale, 'fleet.reconnecting')

  return (
    <p className="fleet__status" data-live={live} role="status">
      <span className="fleet__dot" aria-hidden="true" />
      {label}
    </p>
  )
}

function toneFor(state: string) {
  if (state === 'error' || state === 'offline') return 'bad' as const
  if (state === 'printing' || state === 'finished') return 'good' as const
  if (state === 'paused' || state === 'maintenance') return 'warn' as const
  return 'neutral' as const
}

function formatTime(iso: string, locale: Locale): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleTimeString(locale === 'ru' ? 'ru-RU' : 'en-GB', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Cumulative print hours, grouped and unitless-until-the-suffix.
 *
 * Rounded to whole hours: the column is read to answer "is this machine near its
 * next service", and a decimal place there is noise that costs the eye a
 * comparison. The exact value stays on the wire and in the service card.
 */
function formatHours(hours: string, locale: Locale): string {
  const value = Number(hours)
  if (!Number.isFinite(value)) return hours
  return `${value.toLocaleString(locale === 'ru' ? 'ru-RU' : 'en-GB', {
    maximumFractionDigits: 0,
  })} ${locale === 'ru' ? 'ч' : 'h'}`
}
