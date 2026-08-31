import { useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, translate, translateError } from '@printorian/ui'

import { Field } from './FleetAdmin'

/**
 * A lot, and the two things somebody with `manage_inventory` can do to one.
 *
 * Split out of `MaterialDetail` because they are a different job, not because a
 * counter tripped. That window *reads* a material and draws it; these two
 * **write** — `DELETE /printers/{id}/slots/{unit}/{index}` and
 * `POST /materials/lots` — and each carries the busy flag and the error line
 * that writing needs. Holding both in one file is what took it past the
 * 400-line limit this repository splits at, and the seam was already there.
 *
 * `MaterialLot` lives here with them: it is the shape they act on, and
 * `MaterialsPage` needs it for the location column without wanting anything
 * else the detail window declares.
 */

export interface MaterialLot {
  id: string
  label: string
  remaining_grams: string
  location_kind: string
  shelf: string | null
  printer_id: string | null
  ams_unit: number | null
  ams_slot: number | null
}

export function UnmountButton({
  lot,
  locale,
  onDone,
}: {
  lot: MaterialLot
  locale: Locale
  onDone: () => void
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const [open, setOpen] = useState(false)
  const [shelf, setShelf] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = () => {
    setBusy(true)
    setError(null)
    // The shelf is optional: an operator who has not put the spool away yet
    // still records the removal, and "in stock, place unknown" beats the system
    // believing it is loaded.
    const query = shelf ? `?shelf=${encodeURIComponent(shelf)}` : ''
    api
      .delete(`/printers/${lot.printer_id}/slots/${lot.ams_unit}/${lot.ams_slot}${query}`)
      .then(onDone)
      .catch((exc: unknown) =>
        setError(
          exc instanceof ApiError
            ? translateError(locale, { code: exc.code, details: exc.details })
            : translate(locale, 'error.internal'),
        ),
      )
      .finally(() => setBusy(false))
  }

  if (!open) {
    return (
      <button className="hv-btn hv-btn--sm" type="button" onClick={() => setOpen(true)}>
        {t('materials.unmount')}
      </button>
    )
  }

  return (
    <span className="desk__refund-actions">
      <label className="cfg__field">
        <span>{t('materials.unmount.shelf')}</span>
        <input value={shelf} onChange={(event) => setShelf(event.target.value)} />
      </label>
      <button
        className="hv-btn hv-btn--sm hv-btn--danger"
        type="button"
        disabled={busy}
        onClick={submit}
      >
        {t('common.save')}
      </button>
      <button
        className="hv-btn hv-btn--sm"
        type="button"
        disabled={busy}
        onClick={() => setOpen(false)}
      >
        {t('common.cancel')}
      </button>
      {error && <span className="cfg__error">{error}</span>}
    </span>
  )
}

/**
 * `specCode`, and not the `MaterialSpec` this used to be handed: the code is the
 * only field it ever read, and taking the whole spec tied the one component that
 * writes a lot to the shape of the response that draws one.
 */
export function LotForm({
  specCode,
  locale,
  onDone,
}: {
  specCode: string
  locale: Locale
  onDone: () => void
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const [grams, setGrams] = useState('1000')
  const [shelf, setShelf] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  return (
    <form
      className="admin-detail__row"
      onSubmit={(event) => {
        event.preventDefault()
        setBusy(true)
        setError(null)
        api
          .post('/materials/lots', {
            spec_code: specCode,
            initial_grams: Number(grams),
            shelf: shelf || null,
          })
          .then(onDone)
          .catch((exc: unknown) =>
            setError(
              exc instanceof ApiError
                ? translateError(locale, { code: exc.code, details: exc.details })
                : translate(locale, 'error.internal'),
            ),
          )
          .finally(() => setBusy(false))
      }}
    >
      <Field label={t('materials.lot.grams')}>
        <input
          type="number"
          min="1"
          required
          value={grams}
          onChange={(event) => setGrams(event.target.value)}
        />
      </Field>
      <Field label={t('materials.lot.shelf')}>
        <input value={shelf} onChange={(event) => setShelf(event.target.value)} />
      </Field>
      <button className="hv-btn hv-btn--sm" type="submit" disabled={busy}>
        {t('materials.add_lot')}
      </button>
      {error && (
        <p className="hv-hint hv-bad" role="alert">
          {error}
        </p>
      )}
    </form>
  )
}
