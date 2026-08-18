import { useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, translate, translateError } from '@printorian/ui'

import type { PrinterRow } from './FleetPage'

/**
 * The write half of the fleet screen: adding a machine, replacing its
 * credential, and keeping its service card.
 *
 * Everything here is gated on `manage_fleet` by the caller. The gate is a
 * courtesy — the API enforces it regardless — but offering a button that always
 * fails is its own kind of lie.
 */

const CONNECTION_MODES = ['lan', 'cloud', 'manual'] as const
const SERVICE_KINDS = [
  'nozzle_change',
  'belt_tension',
  'lubrication',
  'bed_level',
  'filter_change',
  'deep_clean',
] as const

function useT(locale: Locale) {
  return (key: MessageKey) => translate(locale, key)
}

function describe(exc: unknown, locale: Locale): string {
  return exc instanceof ApiError
    ? translateError(locale, { code: exc.code, details: exc.details })
    : translate(locale, 'error.internal')
}

// ------------------------------------------------------------- add printer

export function PrinterForm({
  locale,
  onDone,
  onCancel,
}: {
  locale: Locale
  onDone: (printer: PrinterRow) => void
  onCancel: () => void
}) {
  const t = useT(locale)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({
    name: '',
    brand: 'bambu',
    model: '',
    serial: '',
    connection_mode: 'lan' as (typeof CONNECTION_MODES)[number],
    host: '',
    access_code: '',
    location: '',
    acquisition_cost: '0',
    expected_lifetime_hours: '20000',
  })

  const set = (key: keyof typeof form) => (value: string) =>
    setForm((current) => ({ ...current, [key]: value }))

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const created = await api.post<PrinterRow>('/printers', {
        ...form,
        expected_lifetime_hours: Number(form.expected_lifetime_hours),
        // A blank optional field means "not set", not an empty string. Sending
        // "" would store a host nobody can reach and an access code that is set
        // but useless.
        host: form.host || null,
        access_code: form.access_code || null,
        location: form.location || null,
      })
      onDone(created)
    } catch (exc: unknown) {
      setError(describe(exc, locale))
    } finally {
      setBusy(false)
    }
  }

  const lan = form.connection_mode === 'lan'

  return (
    <form className="admin-form" onSubmit={(event) => void submit(event)}>
      <h3>{t('fleet.add.title')}</h3>

      <div className="admin-form__grid">
        <Field label={t('fleet.field.name')}>
          <input required value={form.name} onChange={(e) => set('name')(e.target.value)} />
        </Field>
        <Field label={t('fleet.field.brand')}>
          <input required value={form.brand} onChange={(e) => set('brand')(e.target.value)} />
        </Field>
        <Field label={t('fleet.field.model')}>
          <input value={form.model} onChange={(e) => set('model')(e.target.value)} />
        </Field>
        <Field label={t('fleet.field.serial')}>
          <input value={form.serial} onChange={(e) => set('serial')(e.target.value)} />
        </Field>

        <Field label={t('fleet.field.connection')}>
          <select
            value={form.connection_mode}
            onChange={(e) => set('connection_mode')(e.target.value)}
          >
            {CONNECTION_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {t(`fleet.connection.${mode}` as MessageKey)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t('fleet.field.location')}>
          <input value={form.location} onChange={(e) => set('location')(e.target.value)} />
        </Field>

        {/* Only meaningful for a machine the farm actually connects to; a manual
            printer is driven by a person and has no address or credential. */}
        {lan && (
          <>
            <Field label={t('fleet.field.host')}>
              <input value={form.host} onChange={(e) => set('host')(e.target.value)} />
            </Field>
            <Field label={t('fleet.field.access_code')} hint={t('fleet.access_code.hint')}>
              <input
                type="password"
                autoComplete="off"
                value={form.access_code}
                onChange={(e) => set('access_code')(e.target.value)}
              />
            </Field>
          </>
        )}

        <Field label={t('fleet.field.cost')}>
          <input
            type="number"
            min="0"
            step="0.01"
            value={form.acquisition_cost}
            onChange={(e) => set('acquisition_cost')(e.target.value)}
          />
        </Field>
        <Field label={t('fleet.field.lifetime')}>
          <input
            type="number"
            min="1"
            value={form.expected_lifetime_hours}
            onChange={(e) => set('expected_lifetime_hours')(e.target.value)}
          />
        </Field>
      </div>

      {error && <p className="cfg__error">{error}</p>}

      <div className="admin-form__actions">
        <button type="submit" disabled={busy}>
          {t('common.save')}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>
          {t('common.cancel')}
        </button>
      </div>
    </form>
  )
}

// ------------------------------------------------------------ printer detail

export function PrinterDetail({
  printer,
  locale,
  mayManage,
  onChanged,
  onClose,
}: {
  printer: PrinterRow
  locale: Locale
  mayManage: boolean
  onChanged: (printer: PrinterRow) => void
  onClose: () => void
}) {
  const t = useT(locale)
  const [error, setError] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [kind, setKind] = useState<(typeof SERVICE_KINDS)[number]>('nozzle_change')
  const [interval, setInterval] = useState('500')
  const [busy, setBusy] = useState(false)

  const run = async (work: () => Promise<PrinterRow>) => {
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

  return (
    <section className="admin-detail">
      <header className="admin-detail__head">
        <h3>{printer.name}</h3>
        <button type="button" onClick={onClose}>
          {t('common.close')}
        </button>
      </header>

      <dl className="admin-detail__facts">
        <dt>{t('fleet.field.model')}</dt>
        <dd>{printer.model || '—'}</dd>
        <dt>{t('fleet.field.serial')}</dt>
        <dd>{printer.serial || '—'}</dd>
        <dt>{t('fleet.field.connection')}</dt>
        <dd>{t(`fleet.connection.${printer.connection_mode}` as MessageKey)}</dd>
        <dt>{t('fleet.field.host')}</dt>
        <dd>{printer.host ?? '—'}</dd>
        <dt>{t('fleet.printed_hours')}</dt>
        <dd>{printer.printed_hours}</dd>
        <dt>{t('fleet.amortization')}</dt>
        <dd>{printer.amortization_per_hour}</dd>
        <dt>{t('fleet.field.access_code')}</dt>
        <dd>{t(printer.access_code_set ? 'fleet.access_code.set' : 'fleet.access_code.unset')}</dd>
      </dl>

      {error && <p className="cfg__error">{error}</p>}

      {mayManage && (
        <>
          {/* Write-only: there is no field here that could ever display the
              stored code, because no endpoint returns it (ADR-0014). */}
          <form
            className="admin-detail__row"
            onSubmit={(event) => {
              event.preventDefault()
              void run(async () => {
                const updated = await api.put<PrinterRow>(
                  `/printers/${printer.id}/access-code`,
                  { access_code: code },
                )
                setCode('')
                return updated
              })
            }}
          >
            <label className="cfg__field">
              <span>{t('fleet.access_code.replace')}</span>
              <input
                type="password"
                autoComplete="off"
                required
                value={code}
                onChange={(event) => setCode(event.target.value)}
              />
            </label>
            <button type="submit" disabled={busy}>
              {t('common.save')}
            </button>
          </form>

          <h4>{t('fleet.services')}</h4>
          <form
            className="admin-detail__row"
            onSubmit={(event) => {
              event.preventDefault()
              void run(() =>
                api.post<PrinterRow>(`/printers/${printer.id}/services`, {
                  kind,
                  interval_hours: Number(interval),
                }),
              )
            }}
          >
            <label className="cfg__field">
              <span>{t('fleet.service.kind')}</span>
              <select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}>
                {SERVICE_KINDS.map((value) => (
                  <option key={value} value={value}>
                    {t(`fleet.service.${value}` as MessageKey)}
                  </option>
                ))}
              </select>
            </label>
            <label className="cfg__field">
              <span>{t('fleet.service.interval')}</span>
              <input
                type="number"
                min="1"
                value={interval}
                onChange={(event) => setInterval(event.target.value)}
              />
            </label>
            <button type="submit" disabled={busy}>
              {t('fleet.service.add')}
            </button>
          </form>
        </>
      )}

      {printer.services.length > 0 && (
        <ul className="admin-detail__list">
          {printer.services.map((service) => (
            <li key={service.id} data-due={service.is_due}>
              <span>{t(`fleet.service.${service.kind}` as MessageKey)}</span>
              <span className="admin-detail__muted">
                {service.is_due
                  ? t('fleet.service.due')
                  : `${t('fleet.service.interval')}: ${service.interval_hours}`}
              </span>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    api.post<PrinterRow>(
                      `/printers/${printer.id}/services/${service.id}/complete`,
                    ),
                  )
                }
              >
                {t('fleet.service.complete')}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// ------------------------------------------------------------------ shared

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="cfg__field">
      <span>{label}</span>
      {children}
      {hint && <small className="cfg__hint">{hint}</small>}
    </label>
  )
}
