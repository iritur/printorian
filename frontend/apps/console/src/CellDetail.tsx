import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { Modal, api, translate, translateError } from '@printorian/ui'

import { Field } from './FleetAdmin'

/**
 * One cell's window, read from `GET /store/cells/{address}`.
 *
 * Split out of `StorePage` from the first commit rather than when a counter
 * tripped: `MaterialsPage.tsx` reached 399 lines against a hard 400-line gate and
 * `MaterialDetail.tsx` is the split that saved it. The seam is the same one — the
 * map is a map, and one cell is a different question.
 *
 * The writes live here because this is where a person is already looking at
 * the spool they mean. `POST /store/lots/{id}/place` moves it; the write-off is
 * the irreversible one and is the only path in the system that takes mass off a
 * reel, so it is deliberately a separate control with its own confirmation rather
 * than an option on the move form. `/dry` and `/dried` are the two ends of a trip
 * to the dryer, and the second is the only writer of a spool's drying mark.
 *
 * **The drying column keeps «не сушилась» and «просрочена» apart.** A spool never
 * marked was never measured; one whose mark lapsed was. The backend sends them as
 * two states, and drawing both as lapsed would claim a measurement the farm never
 * took (ADR-0007). `not_required` — PLA, or the rule switched off — is a dash.
 */

/** `CellView`, hand-declared — the console's convention (frontend/CLAUDE.md). */
export interface Cell {
  id: string
  address: string
  zone_code: string
  /** Null when nobody declared one. Not zero, and not one. */
  capacity_lots: number | null
  lot_count: number
  /** Null wherever the capacity was never declared — draw no bar at all. */
  fill_percent: string | null
  is_active: boolean
}

export interface Movement {
  id: string
  lot_id: string
  sequence: number
  /** Machine-readable; rendered from the catalogue (ADR-0012). */
  reason: string
  grams: string
  remaining_after: string
  at: string
  actor_id: string | null
  from_kind: string | null
  from_address: string | null
  to_kind: string | null
  to_address: string | null
  note: string | null
}

/** `DryingView`, computed by the backend at read time and never stored. */
export interface Drying {
  state: 'not_required' | 'drying' | 'unknown' | 'dry' | 'expired'
  dried_at: string | null
  /** Only with a mark to count from: null is "not measured", not "now". */
  valid_until: string | null
  hours_left: string | null
}

/** `StoredLot` — a `LotView` plus what the shelf needs to know. */
export interface Lot {
  id: string
  label: string
  family: string
  remaining_grams: string
  location_kind: string
  cell: string | null
  shelf: string | null
  received_at: string
  drying: Drying
}

interface Detail {
  cell: Cell
  /** FIFO, oldest first — the spool that should leave next is at the top. */
  lots: Lot[]
  /** Null when the rule is off, and the panel says so instead of showing hours. */
  drying_valid_hours: number | null
  movements: Movement[]
}

function DryingCell({ drying, locale }: { drying: Drying; locale: Locale }) {
  switch (drying.state) {
    case 'not_required':
      return <td>—</td>
    case 'drying':
      return (
        <td>
          <span className="hv-state" data-state="maintenance">
            {translate(locale, 'store.drying.drying')}
          </span>
        </td>
      )
    case 'unknown':
      // Never marked. Its own word, not «просрочена»: nothing lapsed.
      return <td className="hv-warn">{translate(locale, 'store.drying.unknown')}</td>
    case 'expired':
      return <td className="hv-warn">{translate(locale, 'store.drying.expired')}</td>
    case 'dry':
      return (
        <td className="hv-good">
          {translate(locale, 'store.drying.dry', {
            until: drying.valid_until ? new Date(drying.valid_until).toLocaleString(locale) : '—',
            hours: drying.hours_left === null ? '—' : Number(drying.hours_left).toFixed(0),
          })}
        </td>
      )
  }
}

export function CellDetail({
  address,
  locale,
  mayManage,
  onClose,
  onChanged,
}: {
  address: string
  locale: Locale
  mayManage: boolean
  onClose: () => void
  onChanged: () => Promise<void>
}) {
  const t = useCallback((key: MessageKey) => translate(locale, key), [locale])
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setDetail(await api.get<Detail>(`/store/cells/${encodeURIComponent(address)}`))
      setError(null)
    } catch (exc: unknown) {
      setDetail(null)
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
    }
  }, [address, locale])

  useEffect(() => {
    // The load is started from an async wrapper rather than called straight from
    // the effect body: `load` sets state, and a setState run synchronously inside
    // an effect is the cascading render `react-hooks/set-state-in-effect` rejects.
    void (async () => {
      await load()
    })()
  }, [load])

  const after = useCallback(async () => {
    await load()
    await onChanged()
  }, [load, onChanged])

  return (
    <Modal
      title={translate(locale, 'store.cell.title', { address })}
      path={`/SUPPLY/STORE/LOCATIONS/${address}`}
      onClose={onClose}
      wide
    >
      {error && <p className="hv-hint hv-bad">{error}</p>}
      {detail && (
        <div className="hv-stack">
          <p className="hv-micro">
            {t('store.cell.capacity')}:{' '}
            {/* Null capacity says so in words. A dash here would be read as zero. */}
            {detail.cell.capacity_lots === null
              ? t('store.fill.unknown')
              : `${detail.cell.lot_count} / ${detail.cell.capacity_lots}`}
          </p>

          <section>
            <div className="hv-panel__head">
              <span>{t('store.cell.lots')}</span>
              <span className="hv-panel__aside">
                {t('store.cell.lots.hint')} ·{' '}
                {detail.drying_valid_hours === null
                  ? t('store.drying.off')
                  : translate(locale, 'store.drying.window', {
                      hours: String(detail.drying_valid_hours),
                    })}
              </span>
            </div>
            {detail.lots.length === 0 ? (
              <p className="hv-hint">{t('store.cell.empty')}</p>
            ) : (
              <table className="hv-table">
                <thead>
                  <tr>
                    <th>{t('materials.lot.label')}</th>
                    <th>{t('store.cell.received')}</th>
                    <th>{t('store.drying')}</th>
                    <th data-align="end">{t('materials.stock')}</th>
                    {mayManage && <th>{t('store.movement.reason')}</th>}
                  </tr>
                </thead>
                <tbody>
                  {detail.lots.map((lot) => (
                    <tr key={lot.id}>
                      <td className="hv-table__id">{lot.label}</td>
                      <td>{new Date(lot.received_at).toLocaleDateString(locale)}</td>
                      <DryingCell drying={lot.drying} locale={locale} />
                      <td data-align="end">{Number(lot.remaining_grams).toFixed(0)}</td>
                      {mayManage && (
                        <td>
                          <LotActions lot={lot} locale={locale} onDone={after} />
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section>
            <div className="hv-panel__head">
              <span>{t('store.movements')}</span>
            </div>
            {detail.movements.length === 0 ? (
              <p className="hv-hint">{t('store.movements.empty')}</p>
            ) : (
              <ul className="hv-stack">
                {detail.movements.map((row) => (
                  <li key={row.id} className="hv-micro">
                    {new Date(row.at).toLocaleString(locale)} ·{' '}
                    {translate(locale, `store.reason.${row.reason}` as MessageKey)} ·{' '}
                    {[row.from_address, row.to_address].map((part) => part ?? '—').join(' → ')}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </Modal>
  )
}

/**
 * Move this spool somewhere else, or take mass off it for good.
 *
 * Two forms rather than one with a mode switch: they have different consequences
 * and only one of them can be undone by doing it again.
 */
function LotActions({
  lot,
  locale,
  onDone,
}: {
  lot: Lot
  locale: Locale
  onDone: () => Promise<void>
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const [target, setTarget] = useState('')
  const [grams, setGrams] = useState('')
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

  /*
    «Высушена» whenever the spool is physically in the dryer — including after
    the rule was switched off, or the button that brings it back disappears with
    the rule and the spool is stuck. «На сушку» only where the rule applies: a
    dash in the column and a button beside it would be two answers.
  */
  const inDryer = lot.location_kind === 'dryer'
  const dryable = !inDryer && lot.location_kind === 'stock' && lot.drying.state !== 'not_required'

  return (
    <div className="hv-stack">
      {failed && <p className="hv-hint hv-bad">{failed}</p>}
      {inDryer && (
        <button
          className="hv-btn hv-btn--sm"
          type="button"
          onClick={() => void send(() => api.post(`/store/lots/${lot.id}/dried`, {}))}
        >
          {t('store.dried')}
        </button>
      )}
      {dryable && (
        <button
          className="hv-btn hv-btn--sm"
          type="button"
          onClick={() => void send(() => api.post(`/store/lots/${lot.id}/dry`, {}))}
        >
          {t('store.to_dryer')}
        </button>
      )}
      <form
        className="hv-row"
        onSubmit={(event) => {
          event.preventDefault()
          void send(async () => {
            await api.post(`/store/lots/${lot.id}/place`, { address: target })
            setTarget('')
          })
        }}
      >
        <Field label={t('store.cell.address')}>
          <input value={target} onChange={(event) => setTarget(event.target.value)} required />
        </Field>
        <button className="hv-btn hv-btn--sm" type="submit">
          {t('store.reason.stock.moved')}
        </button>
      </form>
      <form
        className="hv-row"
        onSubmit={(event) => {
          event.preventDefault()
          void send(async () => {
            await api.post(`/store/lots/${lot.id}/write-off`, { grams })
            setGrams('')
          })
        }}
      >
        <Field label={t('store.movement.grams')}>
          <input
            type="number"
            min={1}
            value={grams}
            onChange={(event) => setGrams(event.target.value)}
            required
          />
        </Field>
        <button className="hv-btn hv-btn--sm" type="submit">
          {t('store.reason.stock.written_off')}
        </button>
      </form>
    </div>
  )
}
