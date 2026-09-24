import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import { api, translate, translateError, useChrome, useSession } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { TicketDetail } from './TicketDetail'
import { formatElapsed, kindKey, originKey } from './format'
import { ALL_KINDS } from './types'
import type { PrinterLabel, ReliabilityReport, Ticket, TicketBoardView, TicketKind } from './types'

/**
 * «Сервис»: the ticket board, and the reliability table beneath it.
 *
 * Two reads, both `VIEW_PRODUCTION`, and both carrying counts and minutes only.
 * The kit heads the page with «ПОТЕРЯ 3 820 ₽» and prices every ticket in a
 * «Последствия» panel; neither figure exists on these routes, by design
 * (`contexts/service/schemas.py`), and this page draws nothing in their place —
 * a ruble panel of em dashes would say "measured, and free".
 *
 * **What the kit draws that this does not, and why**, so the next reader does
 * not port it:
 * - «Запчасти на посту» — inventory knows filament and nothing else.
 * - «Отметки бригады» — no crew record exists; a badge over nothing is a
 *   fabricated denominator.
 * - «Ресурс парка · до ближайшего ТО» — the service card on the fleet screen
 *   already draws it per machine; a second copy here would drift from it.
 *
 * The board is refetched after every write rather than patched, because which
 * lane a ticket lands in is the server's decision (`tickets.lanes_of`), and a
 * step ticked on a merely raised ticket moves it.
 */

const VIEW_PRODUCTION = 'view_production'
const OPERATE_PRINTER = 'operate_printer'

/** «Причины отказов» over the last 90 days, the kit's own window. */
const CAUSES_DAYS = 90

type Lane = 'emergency' | 'planned' | 'in_progress' | 'logistics' | 'closed'
const LANES: readonly Lane[] = ['emergency', 'planned', 'in_progress', 'logistics', 'closed']

function laneKey(lane: Lane): MessageKey {
  return `svc.lane.${lane}` as MessageKey
}

/** Window start for the reliability read: `since` cut to the hour, as the route requires. */
function sinceIso(now: Date, days: number): string {
  const since = new Date(now.getTime() - days * 24 * 3600 * 1000)
  since.setUTCMinutes(0, 0, 0)
  return since.toISOString()
}

export function ServicePage({ locale }: { locale: Locale }) {
  const { actor, ready } = useSession()
  const t = useCallback(
    (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details),
    [locale],
  )

  const [view, setView] = useState<TicketBoardView | null>(null)
  const [reliability, setReliability] = useState<ReliabilityReport | null>(null)
  const [open, setOpen] = useState<Ticket | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [raising, setRaising] = useState(false)
  const [draft, setDraft] = useState<{ kind: TicketKind; title: string; printer_id: string }>({
    kind: 'repair',
    title: '',
    printer_id: '',
  })

  const entitled = actor?.permissions.includes(VIEW_PRODUCTION) ?? false
  const mayOperate = actor?.permissions.includes(OPERATE_PRINTER) ?? false

  const describe = useCallback(
    (exc: unknown) =>
      exc instanceof ApiError
        ? translateError(locale, { code: exc.code, details: exc.details })
        : translate(locale, 'error.internal'),
    [locale],
  )

  const refetch = useCallback(async () => {
    try {
      setView(await api.get<TicketBoardView>('/service/tickets'))
      setError(null)
    } catch (exc: unknown) {
      setError(describe(exc))
    }
  }, [describe])

  useEffect(() => {
    if (!ready || !entitled) return
    void (async () => {
      await refetch()
    })()
    // Its own closure, so a reliability read that fails — the metrics window
    // refuses a `since` it cannot align — leaves the board standing.
    void (async () => {
      try {
        const since = sinceIso(new Date(), CAUSES_DAYS)
        setReliability(
          await api.get<ReliabilityReport>(
            `/service/reliability?since=${encodeURIComponent(since)}`,
          ),
        )
      } catch {
        setReliability(null)
      }
    })()
  }, [ready, entitled, refetch])

  const printers = useMemo(() => {
    const byId = new Map<string, PrinterLabel>()
    for (const printer of view?.printers ?? []) byId.set(printer.id, printer)
    return byId
  }, [view])

  const printerName = (ticket: Ticket): string | null =>
    ticket.printer_id === null ? null : (printers.get(ticket.printer_id)?.name ?? null)

  const openCount = view
    ? view.board.emergency.length +
      view.board.planned.length +
      view.board.in_progress.length +
      view.board.logistics.length
    : null

  useChrome(
    view === null
      ? null
      : {
          path: '/SERVICE/TICKETS',
          meta: [
            { label: 'OPEN', value: String(openCount ?? 0) },
            { label: 'EMERGENCY', value: String(view.board.emergency.length) },
          ],
        },
  )

  const raise = async () => {
    try {
      const created = await api.post<Ticket>('/service/tickets', {
        kind: draft.kind,
        title: draft.title.trim(),
        printer_id: draft.printer_id || null,
      })
      setRaising(false)
      setDraft({ kind: 'repair', title: '', printer_id: '' })
      await refetch()
      setOpen(created)
    } catch (exc: unknown) {
      setError(describe(exc))
    }
  }

  const openTicket = async (id: string) => {
    try {
      setOpen(await api.get<Ticket>(`/service/tickets/${id}`))
    } catch (exc: unknown) {
      setError(describe(exc))
    }
  }

  if (ready && !entitled) return <p className="notice">{t('svc.forbidden')}</p>
  if (!view) return <p className="hv-hint">{error ?? t('common.loading')}</p>

  return (
    <div className="hv-stack hv-stack--4">
      {error && (
        <p className="hv-hint hv-bad" role="alert">
          {error}
        </p>
      )}

      {/* ------------------------------------------------------------ «Заявки» */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('svc.board.title')}</span>
          <span className="hv-panel__aside">{t('svc.board.aside')}</span>
        </div>
        <div className="hv-panel__body hv-stack">
          {LANES.map((lane) => {
            const tickets = view.board[lane]
            return (
              <div key={lane} className="hv-lane" data-lane={lane}>
                <div className="hv-lane__head">
                  <span>{t(laneKey(lane))}</span>
                  <span className="hv-lane__count">{tickets.length}</span>
                </div>
                {tickets.length === 0 ? (
                  <p className="hv-micro">{t('svc.lane.empty')}</p>
                ) : (
                  <ul className="hv-cards">
                    {tickets.map((ticket) => (
                      <li key={ticket.id} className="hv-card" data-origin={ticket.origin}>
                        <button
                          type="button"
                          className="hv-card__button"
                          onClick={() => void openTicket(ticket.id)}
                        >
                          <span className="hv-card__id">{ticket.number}</span>
                          <span className="hv-card__meta">
                            {formatElapsed(ticket.elapsed_seconds, locale)}
                          </span>
                          <span className="hv-card__title">
                            {printerName(ticket) && `${printerName(ticket)} · `}
                            {ticket.title || t(originKey(ticket.origin))}
                          </span>
                          <span className="hv-micro">
                            {t(kindKey(ticket.kind))}
                            {ticket.origin === 'driver' && ` · ${t('svc.origin.driver')}`}
                            {ticket.steps.length > 0 &&
                              ` · ${t('svc.card.steps', { done: ticket.steps_done, total: ticket.steps.length })}`}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )
          })}
        </div>
        <div className="hv-panel__foot">
          <span>{t('svc.board.foot')}</span>
          {mayOperate && (
            <button
              className="hv-btn hv-btn--sm hv-btn--primary"
              type="button"
              onClick={() => setRaising((value) => !value)}
            >
              {t('svc.action.new')}
            </button>
          )}
        </div>
        {raising && (
          <form
            className="hv-panel__body hv-row"
            onSubmit={(event) => {
              event.preventDefault()
              void raise()
            }}
          >
            <select
              aria-label={t('svc.form.kind')}
              value={draft.kind}
              onChange={(event) => setDraft({ ...draft, kind: event.target.value as TicketKind })}
            >
              {ALL_KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {t(kindKey(kind))}
                </option>
              ))}
            </select>
            <select
              aria-label={t('svc.form.printer')}
              value={draft.printer_id}
              onChange={(event) => setDraft({ ...draft, printer_id: event.target.value })}
            >
              <option value="">{t('svc.form.no_printer')}</option>
              {[...printers.values()]
                .filter((printer) => printer.is_active)
                .map((printer) => (
                  <option key={printer.id} value={printer.id}>
                    {printer.name}
                  </option>
                ))}
            </select>
            <input
              aria-label={t('svc.form.title')}
              placeholder={t('svc.form.title')}
              value={draft.title}
              onChange={(event) => setDraft({ ...draft, title: event.target.value })}
            />
            <button
              className="hv-btn hv-btn--primary"
              type="submit"
              disabled={draft.title.trim() === ''}
            >
              {t('svc.action.raise')}
            </button>
          </form>
        )}
      </section>

      {/* ----------------------------------------------------- «Надёжность» */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('svc.reliability.title')}</span>
          <span className="hv-panel__aside">
            {reliability
              ? t('svc.reliability.aside', {
                  reporting: reliability.printers_reporting,
                  listed: reliability.printers_listed,
                })
              : '—'}
          </span>
        </div>
        <div className="hv-panel__body--none">
          <table className="hv-table">
            <thead>
              <tr>
                <th>{t('svc.reliability.printer')}</th>
                <th>{t('svc.reliability.state')}</th>
                <th data-align="end">{t('svc.reliability.failures')}</th>
                <th data-align="end">{t('svc.reliability.rate')}</th>
                <th data-align="end">{t('svc.reliability.mttr')}</th>
              </tr>
            </thead>
            <tbody>
              {(reliability?.rows ?? []).map((row) => (
                <tr key={row.printer_id}>
                  <td>{row.printer_name}</td>
                  <td>{row.state}</td>
                  <td data-align="end">
                    {row.failures}
                    {row.open_failures > 0 &&
                      ` (${t('svc.reliability.open', { count: row.open_failures })})`}
                  </td>
                  {/* Null is "nobody watched this machine" and draws as a dash, never 0. */}
                  <td data-align="end">
                    {row.failures_per_1000_hours === null
                      ? '—'
                      : Number(row.failures_per_1000_hours).toFixed(2)}
                  </td>
                  <td data-align="end">
                    {row.mttr_minutes === null ? '—' : `${Number(row.mttr_minutes).toFixed(0)} м`}
                  </td>
                </tr>
              ))}
              {(reliability?.rows ?? []).length === 0 && (
                <tr>
                  <td colSpan={5}>
                    {reliability ? t('common.empty') : t('svc.reliability.unavailable')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="hv-panel__foot">
          <span>
            {reliability
              ? t('svc.causes.foot', {
                  named: reliability.causes.reduce((sum, cause) => sum + cause.count, 0),
                  uncategorised: reliability.uncategorised,
                })
              : t('svc.reliability.unavailable')}
          </span>
        </div>
      </section>

      {open && (
        <TicketDetail
          ticket={open}
          printerName={printerName(open)}
          locale={locale}
          mayOperate={mayOperate}
          onChanged={(ticket) => {
            setOpen(ticket)
            void refetch()
          }}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  )
}
