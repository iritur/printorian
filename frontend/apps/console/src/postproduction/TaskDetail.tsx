import { useState } from 'react'

import { Modal, api, translate, translateError } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { formatDateTime, formatMinutes, formatStopwatch } from './format'
import { DEFECT_CODES } from './types'
import type { Task } from './types'

/**
 * One task, open: what it is, how to do it, and how long each step should take.
 *
 * The norm sits beside every step *before* the step is started. That is the
 * load-bearing idea of the whole screen — a norm you only hear about when you
 * miss it is a stick, and the same norm next to the checkbox is a gauge that
 * tells a new operator how long the job should take before they begin.
 *
 * The warning block is deliberately loud and deliberately specific. It names the
 * thing that actually causes returns on this model, which is the one piece of
 * writing on the screen that pays for itself.
 */
export function TaskDetail({
  task,
  locale,
  mayAdvance,
  mayInspect,
  onChanged,
  onClose,
}: {
  task: Task
  locale: Locale
  mayAdvance: boolean
  mayInspect: boolean
  onChanged: (task: Task) => void
  onClose: () => void
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) =>
    translate(locale, key, details)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [returning, setReturning] = useState(false)
  const [defect, setDefect] = useState<string>(DEFECT_CODES[0])
  const [note, setNote] = useState('')

  const run = async (work: () => Promise<Task>) => {
    setBusy(true)
    setError(null)
    try {
      onChanged(await work())
    } catch (exc: unknown) {
      setError(describe(exc, locale))
    } finally {
      setBusy(false)
    }
  }

  const nextStep = task.steps.find((step) => step.done_at === null)
  const done = task.steps.filter((step) => step.done_at !== null).length

  return (
    <Modal
      wide
      title={`${t('pp.detail.title')} :: ${task.number}`}
      meta={[
        { label: t('pp.detail.order'), value: task.order_number || '—' },
        { label: t('pp.detail.operation'), value: t(`pp.kind.${task.kind}` as MessageKey) },
      ]}
      status={t(`pp.status.${task.status}` as MessageKey)}
      path={`/PRODUCTION/POSTPROCESS/${task.number}`}
      onClose={onClose}
      footer={
        <>
          <span>
            {task.number} · {task.operator_name || '—'}
          </span>
          <button className="hv-btn hv-btn--sm" type="button" onClick={onClose}>
            {t('common.close')}
          </button>
        </>
      }
    >
          {/* Norm, fact and forecast side by side: the comparison is the whole
              reason for showing any of the three. */}
          <div className="hv-vs">
            <div>
              <span className="hv-vs__k">{t('pp.detail.norm')}</span>
              <span className="hv-vs__v">{formatStopwatch(task.norm_minutes)}</span>
            </div>
            <div>
              <span className="hv-vs__k">{t('pp.detail.elapsed')}</span>
              <span className="hv-vs__v hv-live">{formatStopwatch(task.elapsed_minutes)}</span>
            </div>
            <div>
              <span className="hv-vs__k">{t('pp.detail.projected')}</span>
              <span
                className={
                  overNorm(task) ? 'hv-vs__v hv-warn' : 'hv-vs__v hv-good'
                }
              >
                {task.projected_minutes ? formatStopwatch(task.projected_minutes) : '—'}
              </span>
            </div>
          </div>

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('pp.detail.what')}</span>
              <span className="hv-panel__aside">
                {task.model_name || '—'} · {t('pp.card.units', { count: task.quantity })}
              </span>
            </div>
            <div className="hv-panel__body hv-panel__body--tight">
              <ul className="hv-leaders">
                <Leader label={t('pp.detail.material')} value={task.material_code || '—'} />
                <Leader
                  label={t('pp.detail.colors')}
                  value={task.colors.length > 0 ? task.colors.join(' · ') : '—'}
                />
                <Leader
                  label={t('pp.detail.due')}
                  value={task.due_at ? formatDateTime(task.due_at, locale) : '—'}
                />
                {task.attempt > 1 && (
                  <Leader label={t('pp.detail.attempt')} value={String(task.attempt)} tone="warn" />
                )}
                {task.defect_code && (
                  <Leader
                    label={t('pp.detail.defect')}
                    value={translate(locale, task.defect_code as MessageKey)}
                    tone="bad"
                  />
                )}
              </ul>
            </div>
          </section>

          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('pp.detail.instruction')}</span>
              <span className="hv-panel__aside">
                {t('pp.card.step', { position: done + 1, total: task.steps.length })}
              </span>
            </div>
            {task.steps.length === 0 ? (
              <div className="hv-panel__body">
                <p className="hv-hint">{t('pp.detail.no_steps')}</p>
              </div>
            ) : (
              <div className="hv-steps">
                {task.steps.map((step) => (
                  <div
                    className="hv-step"
                    key={step.position}
                    {...(step.done_at ? { 'data-done': 'true' } : {})}
                  >
                    <span>
                      <span className="hv-step__t">{step.title}</span>
                      {step.detail && <span className="hv-step__d">{step.detail}</span>}
                      {step.warning && (
                        <span className="hv-step__warn">
                          <b>{t('pp.detail.warning')}</b> {step.warning}
                        </span>
                      )}
                    </span>
                    <span className="hv-step__n">
                      {t('pp.detail.step_norm', { norm: Math.round(Number(step.norm_minutes)) })}
                      {step.actual_minutes !== null && (
                        <>
                          <br />
                          {t('pp.detail.step_fact', {
                            actual: Math.round(Number(step.actual_minutes)),
                          })}
                        </>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <div className="hv-panel__foot">
              <span>{t('pp.detail.version', { version: task.instruction_version || '—' })}</span>
              <span>
                {formatMinutes(task.elapsed_minutes, locale)} /{' '}
                {formatMinutes(task.norm_minutes, locale)}
              </span>
            </div>
          </section>

          {error && <p className="hv-error">{error}</p>}

          {returning ? (
            <section className="hv-frame hv-stack">
              <label className="hv-field">
                <span className="hv-label">{t('pp.action.defect_code')}</span>
                <select
                  className="hv-select"
                  value={defect}
                  onChange={(event) => setDefect(event.target.value)}
                >
                  {DEFECT_CODES.map((code) => (
                    <option value={code} key={code}>
                      {translate(locale, code as MessageKey)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="hv-field">
                <span className="hv-label">{t('pp.action.defect_note')}</span>
                <textarea
                  className="hv-textarea"
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  rows={2}
                />
              </label>
              <div className="hv-row">
                <button
                  className="hv-btn hv-btn--danger"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      api.post<Task>(`/postproduction/tasks/${task.id}/return`, {
                        defect_code: defect,
                        note: note || null,
                      }),
                    )
                  }
                >
                  {t('pp.action.confirm_return')}
                </button>
                <button className="hv-btn" type="button" onClick={() => setReturning(false)}>
                  {t('common.cancel')}
                </button>
              </div>
            </section>
          ) : (
            <div className="hv-row">
              {mayAdvance && canStart(task) && (
                <button
                  className="hv-btn hv-btn--primary hv-btn--lg"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void run(() => api.post<Task>(`/postproduction/tasks/${task.id}/start`, {}))
                  }
                >
                  {task.status === 'waiting' ? t('pp.action.start') : t('pp.action.resume')}
                </button>
              )}
              {mayAdvance && task.status === 'in_progress' && nextStep && (
                <button
                  className="hv-btn hv-btn--primary hv-btn--lg"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void run(() =>
                      api.post<Task>(`/postproduction/tasks/${task.id}/steps`, {
                        position: nextStep.position,
                      }),
                    )
                  }
                >
                  {t('pp.action.step')}
                </button>
              )}
              {mayAdvance && task.status === 'in_progress' && (
                <>
                  {!nextStep && (
                    <button
                      className="hv-btn hv-btn--primary hv-btn--lg"
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void run(() =>
                          api.post<Task>(`/postproduction/tasks/${task.id}/finish`, {}),
                        )
                      }
                    >
                      {t('pp.action.finish')}
                    </button>
                  )}
                  <button
                    className="hv-btn"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void run(() => api.post<Task>(`/postproduction/tasks/${task.id}/pause`, {}))
                    }
                  >
                    {t('pp.action.pause')}
                  </button>
                </>
              )}
              {mayInspect && task.status === 'for_qc' && (
                <>
                  <button
                    className="hv-btn hv-btn--primary hv-btn--lg"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void run(() => api.post<Task>(`/postproduction/tasks/${task.id}/pass`, {}))
                    }
                  >
                    {t('pp.action.pass')}
                  </button>
                  <span className="hv-spacer" />
                  <button
                    className="hv-btn hv-btn--danger"
                    type="button"
                    onClick={() => setReturning(true)}
                  >
                    {t('pp.action.return')}
                  </button>
                </>
              )}
            </div>
          )}
    </Modal>
  )
}

function Leader({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <li className="hv-leader" {...(tone ? { 'data-tone': tone } : {})}>
      <span className="hv-leader__k">{label}</span>
      <span className="hv-leader__fill" />
      <span className="hv-leader__v">{value}</span>
    </li>
  )
}

/** Whether the operator may pick this up — the server's state machine, mirrored. */
function canStart(task: Task): boolean {
  return task.status === 'waiting' || task.status === 'paused' || task.status === 'returned'
}

function overNorm(task: Task): boolean {
  if (task.projected_minutes === null) return false
  return Number(task.projected_minutes) > Number(task.norm_minutes)
}

function describe(exc: unknown, locale: Locale): string {
  const body = exc as { code?: string; details?: Record<string, unknown> }
  if (typeof body?.code !== 'string') return translate(locale, 'error.internal')
  // `details` is spread rather than passed as a possibly-undefined property:
  // `exactOptionalPropertyTypes` distinguishes "absent" from "present and
  // undefined", and the catalogue's interpolation only cares about the former.
  return translateError(locale, {
    code: body.code,
    ...(body.details ? { details: body.details } : {}),
  })
}
