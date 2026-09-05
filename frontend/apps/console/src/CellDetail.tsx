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
 * The two writes live here because this is where a person is already looking at
 * the spool they mean. `POST /store/lots/{id}/place` moves it; the write-off is
 * the irreversible one and is the only path in the system that takes mass off a
 * reel, so it is deliberately a separate control with its own confirmation rather
 * than an option on the move form.
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

interface Lot {
  id: string
  label: string
  remaining_grams: string
  location_kind: string
  cell: string | null
  shelf: string | null
}

interface Detail {
  cell: Cell
  /** FIFO, oldest first — the spool that should leave next is at the top. */
  lots: Lot[]
  movements: Movement[]
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
    void load()
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
              <span className="hv-panel__aside">{t('store.cell.lots.hint')}</span>
            </div>
            {detail.lots.length === 0 ? (
              <p className="hv-hint">{t('store.cell.empty')}</p>
            ) : (
              <table className="hv-table">
                <thead>
                  <tr>
                    <th>{t('materials.lot.label')}</th>
                    <th data-align="end">{t('materials.stock')}</th>
                    {mayManage && <th>{t('store.movement.reason')}</th>}
                  </tr>
                </thead>
                <tbody>
                  {detail.lots.map((lot) => (
                    <tr key={lot.id}>
                      <td className="hv-table__id">{lot.label}</td>
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

  return (
    <div className="hv-stack">
      {failed && <p className="hv-hint hv-bad">{failed}</p>}
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
