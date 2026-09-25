import { useState } from 'react'

import { ApiError } from '@printorian/api-client'
import { api, translate, translateError } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { formatElapsed, kindKey, originKey } from './format'
import type { Ticket } from './types'

/**
 * One ticket: «Что случилось», «Порядок работ», and the buttons that move it.
 *
 * The kit's «Последствия» panel — what the ticket has cost — is not drawn. It is
 * money, the route carries none, and a panel of em dashes under that heading
 * would read as "measured, and free" (ADR-0007).
 *
 * Refetched from the server after every write rather than patched: a step ticked
 * on a merely raised ticket starts it, and the lane it moves to is the server's
 * decision. The parent re-reads the board for the same reason.
 */
export function TicketDetail({
  ticket,
  printerName,
  locale,
  mayOperate,
  onChanged,
  onClose,
}: {
  ticket: Ticket
  printerName: string | null
  locale: Locale
  mayOperate: boolean
  onChanged: (ticket: Ticket) => void
  onClose: () => void
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)
  const [error, setError] = useState<string | null>(null)
  const [stepTitle, setStepTitle] = useState('')

  // Each action names its full path in one literal, rather than a base plus a
  // suffix: `tests/unit/test_docs_endpoint_consumers.py` reads the console's
  // path literals to know which routes a screen consumes, and a suffix passed
  // as a separate string is invisible to it.
  const act = async (path: string, body?: unknown) => {
    try {
      const next = await api.post<Ticket>(path, body ?? {})
      setError(null)
      onChanged(next)
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
    }
  }

  const closed = ticket.status === 'closed'
  const pending = ticket.steps.length - ticket.steps_done
  const heading = ticket.title || t(originKey(ticket.origin))

  return (
    <div className="hv-overlay" role="dialog" aria-modal="true" aria-labelledby="svc-ticket-t">
      <div className="hv-modal">
        <div className="hv-modal__body hv-stack">
          <div className="hv-row hv-row--between">
            <h3 id="svc-ticket-t">
              {ticket.number} · {heading}
            </h3>
            <button
              className="hv-os__x"
              type="button"
              onClick={onClose}
              aria-label={t('common.close')}
            >
              ✕
            </button>
          </div>

          {error && (
            <p className="hv-hint hv-bad" role="alert">
              {error}
            </p>
          )}

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('svc.detail.what')}</span>
              <span className="hv-panel__aside">
                {ticket.origin === 'driver' ? t('svc.origin.driver') : t('svc.origin.person')}
              </span>
            </div>
            <div className="hv-panel__body">
              <dl className="hv-kv">
                <dt>{t('svc.detail.kind')}</dt>
                <dd>{t(kindKey(ticket.kind))}</dd>
                <dt>{t('svc.detail.printer')}</dt>
                <dd>{printerName ?? '—'}</dd>
                <dt>{t('svc.detail.elapsed')}</dt>
                <dd>
                  {formatElapsed(ticket.elapsed_seconds, locale)}
                  {ticket.norm_minutes !== null &&
                    ` · ${t('svc.detail.norm', { minutes: ticket.norm_minutes })}`}
                </dd>
                {ticket.note && (
                  <>
                    <dt>{t('svc.detail.note')}</dt>
                    <dd>{ticket.note}</dd>
                  </>
                )}
              </dl>
            </div>
          </section>

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('svc.steps.title')}</span>
              <span className="hv-panel__aside">
                {t('svc.steps.aside', { done: ticket.steps_done, total: ticket.steps.length })}
              </span>
            </div>
            <div className="hv-panel__body--none">
              {ticket.steps.length === 0 ? (
                <p className="hv-hint" style={{ padding: 'var(--hv-3)' }}>
                  {t('svc.steps.empty')}
                </p>
              ) : (
                <ol className="hv-steps">
                  {ticket.steps.map((step) => (
                    <li key={step.position} className="hv-step" data-done={step.done_at !== null}>
                      <span className="hv-step__title">
                        {step.position}. {step.title}
                        {step.norm_minutes !== null && (
                          <span className="hv-micro">
                            {' '}
                            · {t('svc.detail.norm', { minutes: step.norm_minutes })}
                          </span>
                        )}
                      </span>
                      {step.note && <span className="hv-micro">{step.note}</span>}
                      {mayOperate && !closed && step.done_at === null && (
                        <button
                          className="hv-btn hv-btn--sm"
                          type="button"
                          onClick={() =>
                            void act(`/service/tickets/${ticket.id}/steps/${step.position}/done`)
                          }
                        >
                          {t('svc.steps.done')}
                        </button>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </div>
            {mayOperate && !closed && (
              <div className="hv-panel__foot">
                <input
                  aria-label={t('svc.steps.add_title')}
                  placeholder={t('svc.steps.add_title')}
                  value={stepTitle}
                  onChange={(event) => setStepTitle(event.target.value)}
                />
                <button
                  className="hv-btn hv-btn--sm"
                  type="button"
                  disabled={stepTitle.trim() === ''}
                  onClick={() => {
                    void act(`/service/tickets/${ticket.id}/steps`, { title: stepTitle.trim() })
                    setStepTitle('')
                  }}
                >
                  {t('svc.steps.add')}
                </button>
              </div>
            )}
          </section>

          {mayOperate && !closed && (
            <div className="hv-row">
              {ticket.status === 'open' && (
                <button
                  className="hv-btn"
                  type="button"
                  onClick={() => void act(`/service/tickets/${ticket.id}/start`)}
                >
                  {t('svc.action.start')}
                </button>
              )}
              {/*
                Disabled rather than hidden while a step is unticked: the server
                refuses the close with `error.service.steps_pending`, and a
                button that explains why it cannot be pressed is the kit's rule
                for irreversible controls.
              */}
              <button
                className="hv-btn hv-btn--primary"
                type="button"
                disabled={pending > 0}
                title={pending > 0 ? t('svc.action.close_pending', { count: pending }) : undefined}
                onClick={() => void act(`/service/tickets/${ticket.id}/close`)}
              >
                {t('svc.action.close')}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
