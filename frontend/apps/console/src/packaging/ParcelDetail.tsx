import { useState } from 'react'

import { Modal, api, translate, translateError } from '@printorian/ui'
import type { Locale, MessageKey } from '@printorian/ui'

import { formatClock, formatCountdown, formatDims, formatGrams, formatMoney } from './format'
import { DISCREPANCY_CODES, HOLD_REASONS } from './types'
import type { HoldReason, Parcel, TaraRow } from './types'

/**
 * One parcel, open: what goes in it, what it goes in, and what to do in order.
 *
 * Three things are on the screen at once because a packer needs all three before
 * the box is closed, and finding out afterwards is expensive in every case:
 *
 * **Completeness**, against what the customer actually bought — read from the
 * order, not copied onto the parcel, so there is exactly one answer to "how many
 * did they order".
 *
 * **The box the geometry implies**, beside whatever the packer reached for. It
 * never refuses their choice: a rule that overrode the bench would be worked
 * around within a week and the farm would lose the measurement as well as the
 * argument.
 *
 * **The instruction**, with the norm on every step *before* the step is started
 * — the same load-bearing idea as the finishing post, and the warning block is
 * the one piece of writing here that has already paid for itself.
 */
export function ParcelDetail({
  parcel,
  tara,
  locale,
  mayPack,
  onChanged,
  onClose,
}: {
  parcel: Parcel
  tara: TaraRow[]
  locale: Locale
  mayPack: boolean
  onChanged: (parcel: Parcel) => void
  onClose: () => void
}) {
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState<'none' | 'discrepancy' | 'hold'>('none')
  const [code, setCode] = useState<string>(DISCREPANCY_CODES[0])
  const [reason, setReason] = useState<HoldReason>(HOLD_REASONS[0] as HoldReason)
  const [note, setNote] = useState('')
  const [weight, setWeight] = useState('')

  const run = async (work: () => Promise<Parcel>) => {
    setBusy(true)
    setError(null)
    try {
      onChanged(await work())
      setMode('none')
    } catch (exc: unknown) {
      setError(describe(exc, locale))
    } finally {
      setBusy(false)
    }
  }

  const post = (path: string, body: Record<string, unknown> = {}) =>
    api.post<Parcel>(`/packaging/parcels/${parcel.id}/${path}`, body)

  const nextStep = parcel.steps.find((step) => step.done_at === null)
  const boxes = tara.filter((row) => row.kind === 'box' || row.kind === 'bag')

  return (
    <Modal
      wide
      title={`${t('pk.detail.title')} :: ${parcel.number}`}
      meta={[
        { label: t('pk.detail.order'), value: parcel.order_number || '—' },
        {
          label: t('pk.detail.delivery'),
          value:
            parcel.carrier_code ||
            translate(locale, `pk.method.${parcel.delivery_method}` as MessageKey),
        },
        { label: t('pk.detail.cutoff'), value: formatClock(parcel.cutoff_at, locale) },
      ]}
      status={t(`pk.status.${parcel.status}` as MessageKey)}
      path={`/PRODUCTION/PACKING/${parcel.number}`}
      onClose={onClose}
      footer={
        <>
          <span>
            {parcel.number} · {parcel.operator_name || '—'}
          </span>
          <button className="hv-btn hv-btn--sm" type="button" onClick={onClose}>
            {t('common.close')}
          </button>
        </>
      }
    >
      {/* Norm, fact and the van, side by side. The comparison is the whole
          reason for showing any of the three. */}
      <div className="hv-vs">
        <div>
          <span className="hv-vs__k">{t('pk.detail.norm')}</span>
          <span className="hv-vs__v">{formatCountdown(parcel.norm_minutes, locale)}</span>
        </div>
        <div>
          <span className="hv-vs__k">{t('pk.detail.elapsed')}</span>
          <span className="hv-vs__v hv-live">
            {formatCountdown(parcel.elapsed_minutes, locale)}
          </span>
        </div>
        <div>
          <span className="hv-vs__k">{t('pk.detail.to_cutoff')}</span>
          <span className={parcel.urgency === 'ok' ? 'hv-vs__v' : 'hv-vs__v hv-warn'}>
            {parcel.minutes_to_cutoff === null
              ? '—'
              : formatCountdown(parcel.minutes_to_cutoff, locale)}
          </span>
        </div>
      </div>

      <div className="hv-cols hv-cols--2">
        {/* ------------------------------------------------ completeness */}
        <section className="hv-panel">
          <div className="hv-panel__head">
            <span>{t('pk.detail.contents')}</span>
            <span className="hv-panel__aside">{t('pk.detail.contents_aside')}</span>
          </div>
          <div className="hv-panel__body--none">
            {parcel.lines.length === 0 ? (
              <p className="hv-hint" style={{ padding: 'var(--hv-3)' }}>
                {t('pk.detail.contents_empty')}
              </p>
            ) : (
              <table className="hv-table">
                <thead>
                  <tr>
                    <th>{t('pk.detail.line')}</th>
                    <th data-align="end">{t('pk.detail.ordered')}</th>
                    <th data-align="end">{t('pk.detail.present')}</th>
                  </tr>
                </thead>
                <tbody>
                  {parcel.lines.map((line) => (
                    <tr key={`${line.model_name}-${line.color}`}>
                      <td>
                        {line.model_name}
                        {line.color && ` · ${line.color}`}
                      </td>
                      <td data-align="end">{line.ordered}</td>
                      <td data-align="end">{line.present}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div className="hv-panel__foot">
            <span>{t('pk.detail.items_total', { count: parcel.items })}</span>
            <span className={parcel.discrepancy_code ? 'hv-bad' : 'hv-good'}>
              {parcel.discrepancy_code
                ? translate(locale, parcel.discrepancy_code as MessageKey)
                : t('pk.detail.no_discrepancy')}
            </span>
          </div>
        </section>

        {/* ------------------------------------------------- tara and mass */}
        <section className="hv-panel">
          <div className="hv-panel__head">
            <span>{t('pk.detail.tara')}</span>
            <span className="hv-panel__aside">{t('pk.detail.tara_aside')}</span>
          </div>
          <div className="hv-panel__body hv-panel__body--tight">
            <ul className="hv-leaders">
              <Leader
                label={t('pk.detail.recommended')}
                basis={t('pk.detail.recommended_basis')}
                value={parcel.recommended_tara_name || t('pk.detail.nothing_fits')}
                {...(parcel.recommended_tara_name ? {} : { tone: 'warn' })}
              />
              <Leader
                label={t('pk.detail.batch_size')}
                value={formatDims(parcel.length_mm, parcel.width_mm, parcel.height_mm, locale)}
              />
              <Leader
                label={t('pk.detail.estimated_weight')}
                value={formatGrams(parcel.estimated_grams, locale)}
              />
              <Leader
                label={t('pk.detail.volumetric')}
                basis={t('pk.detail.volumetric_basis')}
                value={formatGrams(parcel.volumetric_grams, locale)}
              />
              <Leader
                label={t('pk.detail.weighed')}
                value={formatGrams(parcel.weight_grams, locale)}
              />
              {parcel.wrap_required && (
                <Leader
                  label={t('pk.detail.wrap')}
                  basis={t('pk.detail.wrap_basis')}
                  value={t('common.yes')}
                  tone="warn"
                />
              )}
            </ul>
            <hr className="hv-hr" />
            <div className="hv-slab">
              <span>{t('pk.detail.cost')}</span>
              <span className="hv-slab__v">{formatMoney(parcel.packaging_cost, locale)}</span>
            </div>
          </div>
          {mayPack && !parcel.status.match(/shipped|cancelled/) && (
            <div className="hv-panel__foot">
              <label className="hv-field" style={{ flex: '1 1 auto' }}>
                <span className="hv-label">{t('pk.action.choose_tara')}</span>
                <select
                  className="hv-select"
                  value={parcel.tara_id ?? parcel.recommended_tara_id ?? ''}
                  disabled={busy}
                  onChange={(event) =>
                    void run(() => post('tara', { tara_id: event.target.value, extras: {} }))
                  }
                >
                  <option value="">—</option>
                  {boxes.map((row) => (
                    <option value={row.id} key={row.id}>
                      {row.name} · {formatMoney(row.price, locale)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="hv-field" style={{ flex: '0 0 140px' }}>
                <span className="hv-label">{t('pk.action.weigh')}</span>
                <input
                  className="hv-input"
                  inputMode="numeric"
                  value={weight}
                  disabled={busy}
                  onChange={(event) => setWeight(event.target.value)}
                  onBlur={() => {
                    if (weight.trim() === '') return
                    void run(() => post('weight', { weight_grams: weight }))
                  }}
                />
              </label>
            </div>
          )}
        </section>
      </div>

      {/* ------------------------------------------------------ instruction */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('pk.detail.instruction')}</span>
          <span className="hv-panel__aside">
            {t('pk.detail.step_of', {
              position: Math.min(nextStep?.position ?? parcel.steps.length, parcel.steps.length),
              total: parcel.steps.length,
            })}
          </span>
        </div>
        <div className="hv-steps">
          {parcel.steps.length === 0 && (
            <p className="hv-hint" style={{ padding: 'var(--hv-3)' }}>
              {t('pk.detail.no_instruction')}
            </p>
          )}
          {parcel.steps.map((step) => (
            <div
              className="hv-step"
              key={step.position}
              {...(step.done_at ? { 'data-done': 'true' } : {})}
            >
              <span>
                <span className="hv-step__t">{step.title}</span>
                {step.detail && <span className="hv-step__d">{step.detail}</span>}
                {step.warning && parcel.wrap_required && (
                  <span className="hv-step__warn">{step.warning}</span>
                )}
              </span>
              <span className="hv-step__n">
                {formatCountdown(step.norm_minutes, locale)}
                {step.actual_minutes !== null && (
                  <>
                    <br />
                    {t('pk.detail.fact', {
                      value: formatCountdown(step.actual_minutes, locale),
                    })}
                  </>
                )}
              </span>
            </div>
          ))}
        </div>
        <div className="hv-panel__foot">
          <span>{t('pk.detail.version', { version: parcel.instruction_version || '—' })}</span>
          <span>
            {t('pk.detail.of_norm', {
              elapsed: formatCountdown(parcel.elapsed_minutes, locale),
              norm: formatCountdown(parcel.norm_minutes, locale),
            })}
          </span>
        </div>
      </section>

      {error && <p className="notice notice--error">{error}</p>}

      {mode === 'discrepancy' ? (
        <Reasoned
          locale={locale}
          title={t('pk.action.discrepancy')}
          options={DISCREPANCY_CODES.map((one) => [one, translate(locale, one as MessageKey)])}
          value={code}
          onValue={setCode}
          note={note}
          onNote={setNote}
          busy={busy}
          onConfirm={() =>
            void run(() => post('discrepancy', { discrepancy_code: code, note: note || null }))
          }
          onCancel={() => setMode('none')}
        />
      ) : mode === 'hold' ? (
        <Reasoned
          locale={locale}
          title={t('pk.action.hold')}
          options={HOLD_REASONS.map((one) => [
            one,
            translate(locale, `pk.hold.${one}` as MessageKey),
          ])}
          value={reason}
          onValue={(next) => setReason(next as HoldReason)}
          note={note}
          onNote={setNote}
          busy={busy}
          onConfirm={() => void run(() => post('hold', { reason, note: note || null }))}
          onCancel={() => setMode('none')}
        />
      ) : (
        <div className="hv-row">
          {mayPack && parcel.status === 'checked' && (
            <button
              className="hv-btn hv-btn--primary hv-btn--lg"
              type="button"
              disabled={busy}
              onClick={() => void run(() => post('start'))}
            >
              {t('pk.action.start')}
            </button>
          )}
          {mayPack && parcel.status === 'packing' && nextStep && (
            <button
              className="hv-btn hv-btn--primary hv-btn--lg"
              type="button"
              disabled={busy}
              onClick={() => void run(() => post('steps', { position: nextStep.position }))}
            >
              {t('pk.action.step')}
            </button>
          )}
          {mayPack && parcel.status === 'packing' && !nextStep && (
            <button
              className="hv-btn hv-btn--primary hv-btn--lg"
              type="button"
              disabled={busy}
              onClick={() => void run(() => post('ready'))}
            >
              {t('pk.action.ready')}
            </button>
          )}
          {mayPack && parcel.status === 'ready' && (
            <button
              className="hv-btn hv-btn--primary hv-btn--lg"
              type="button"
              disabled={busy}
              onClick={() => void run(() => post('ship'))}
            >
              {t('pk.action.ship')}
            </button>
          )}
          {mayPack && parcel.status === 'held' && (
            <button
              className="hv-btn hv-btn--primary hv-btn--lg"
              type="button"
              disabled={busy}
              onClick={() => void run(() => post('release'))}
            >
              {t('pk.action.release')}
            </button>
          )}
          {mayPack && parcel.status !== 'held' && parcel.status !== 'shipped' && (
            <>
              <button
                className="hv-btn"
                type="button"
                disabled={busy}
                onClick={() => setMode('hold')}
              >
                {t('pk.action.hold')}
              </button>
              <span className="hv-spacer" />
              <button
                className="hv-btn hv-btn--danger"
                type="button"
                disabled={busy}
                onClick={() => setMode('discrepancy')}
              >
                {t('pk.action.discrepancy')}
              </button>
            </>
          )}
        </div>
      )}
    </Modal>
  )
}

/**
 * A code and a sentence, for the two actions that must not be anonymous.
 *
 * Both a hold and a discrepancy are read later by somebody who was not there, so
 * neither is allowed to be a bare status change: the code drives the figures the
 * post is judged on, and the note is what the person who has to fix it reads.
 */
function Reasoned({
  locale,
  title,
  options,
  value,
  onValue,
  note,
  onNote,
  busy,
  onConfirm,
  onCancel,
}: {
  locale: Locale
  title: string
  options: [string, string][]
  value: string
  onValue: (value: string) => void
  note: string
  onNote: (value: string) => void
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const t = (key: MessageKey) => translate(locale, key)
  return (
    <section className="hv-frame hv-stack">
      <span className="hv-label">{title}</span>
      <label className="hv-field">
        <span className="hv-label">{t('pk.action.reason')}</span>
        <select
          className="hv-select"
          value={value}
          onChange={(event) => onValue(event.target.value)}
        >
          {options.map(([code, label]) => (
            <option value={code} key={code}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label className="hv-field">
        <span className="hv-label">{t('pk.action.note')}</span>
        <textarea
          className="hv-textarea"
          value={note}
          onChange={(event) => onNote(event.target.value)}
          rows={2}
        />
      </label>
      <div className="hv-row">
        <button className="hv-btn hv-btn--danger" type="button" disabled={busy} onClick={onConfirm}>
          {t('common.confirm')}
        </button>
        <button className="hv-btn" type="button" onClick={onCancel}>
          {t('common.cancel')}
        </button>
      </div>
    </section>
  )
}

function Leader({
  label,
  basis,
  value,
  tone,
}: {
  label: string
  basis?: string
  value: string
  tone?: string
}) {
  return (
    <li className="hv-leader" {...(tone ? { 'data-tone': tone } : {})}>
      <span className="hv-leader__k">
        {label}
        {basis && <span className="hv-leader__basis">{basis}</span>}
      </span>
      <span className="hv-leader__fill" />
      <span className="hv-leader__v">{value}</span>
    </li>
  )
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
