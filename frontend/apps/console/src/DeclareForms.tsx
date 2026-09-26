import { useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, translate, translateError } from '@printorian/ui'

import { Field } from './FleetAdmin'

/**
 * Declaring a zone and a cell, in the map panel's foot.
 *
 * Without this the two POST routes have no path literal anywhere under
 * `frontend/apps/<app>/src` and the endpoint-consumer gate fails — but the gate is
 * the symptom. The real point is that a warehouse screen with no way to declare a
 * cell is a mechanism the product cannot reach, which is the #58 finding HANDOFF
 * records in as many words.
 *
 * `capacity_lots` is left blank by default and sent as absent when blank. That is
 * the whole ADR-0007 decision reaching the form: an operator who does not know how
 * many spools fit must be able to say nothing rather than be made to guess.
 */
export function DeclareForms({ locale, onDone }: { locale: Locale; onDone: () => Promise<void> }) {
  const t = (key: MessageKey) => translate(locale, key)
  const [zoneCode, setZoneCode] = useState('')
  const [zoneName, setZoneName] = useState('')
  const [cellZone, setCellZone] = useState('')
  const [address, setAddress] = useState('')
  const [capacity, setCapacity] = useState('')
  const [failed, setFailed] = useState<string | null>(null)

  const send = async (run: () => Promise<unknown>) => {
    try {
      await run()
      setFailed(null)
      await onDone()
    } catch (exc: unknown) {
      setFailed(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
    }
  }

  return (
    <div className="hv-stack">
      {failed && <p className="hv-hint hv-bad">{failed}</p>}
      <form
        className="hv-row"
        onSubmit={(event) => {
          event.preventDefault()
          void send(async () => {
            await api.post('/store/zones', { code: zoneCode, name: zoneName })
            setZoneCode('')
            setZoneName('')
          })
        }}
      >
        <Field label={t('store.zone.code')}>
          <input value={zoneCode} onChange={(event) => setZoneCode(event.target.value)} required />
        </Field>
        <Field label={t('store.zone.name')}>
          <input value={zoneName} onChange={(event) => setZoneName(event.target.value)} />
        </Field>
        <button className="hv-btn hv-btn--sm" type="submit">
          {t('store.add_zone')}
        </button>
      </form>
      <form
        className="hv-row"
        onSubmit={(event) => {
          event.preventDefault()
          void send(async () => {
            await api.post('/store/cells', {
              zone_code: cellZone,
              address,
              // Blank means "nobody has said", and the backend answers no fill for
              // it. Sending 0 or 1 here would be the invented number.
              capacity_lots: capacity === '' ? null : Number(capacity),
            })
            setAddress('')
            setCapacity('')
          })
        }}
      >
        <Field label={t('store.zone.code')}>
          <input value={cellZone} onChange={(event) => setCellZone(event.target.value)} required />
        </Field>
        <Field label={t('store.cell.address')}>
          <input value={address} onChange={(event) => setAddress(event.target.value)} required />
        </Field>
        <Field label={t('store.cell.capacity_lots')} hint={t('store.fill.unknown')}>
          <input
            type="number"
            min={1}
            value={capacity}
            onChange={(event) => setCapacity(event.target.value)}
          />
        </Field>
        <button className="hv-btn hv-btn--sm" type="submit">
          {t('store.add_cell')}
        </button>
      </form>
    </div>
  )
}
