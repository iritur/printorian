import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, translate, translateError, useChrome, useSession } from '@printorian/ui'

import { CellDetail } from './CellDetail'
// `Cell` and `Movement` are declared beside the window rather than here, so the
// dependency runs one way: the map imports the detail, and the detail imports
// nothing back. Both shapes appear in `GET /store/cells/{address}` as well as in
// the map, so either file could have owned them and only one ordering is acyclic.
import type { Cell, Movement } from './CellDetail'
import { Field } from './FleetAdmin'

/**
 * The store (design/store.html): the cell map by zone, and the movement ledger.
 *
 * **Two KPI tiles, where the kit draws four, and the two that are missing are the
 * point.** «Стоимость остатков» and «Залежалое» are money, and nothing in the
 * system writes `MaterialLot.purchase_price` — so both would read `0 ₽` over a
 * farm holding several hundred thousand roubles of filament, which is an invented
 * number in a nicer font (ADR-0007). «Расхождения» needs a stocktake that does not
 * exist. `DiagnosticsPanel` made the same call when it dropped «Версии» and
 * «Журнал» rather than filling them with placeholders. «Ячеек» and «Заполнение»
 * are both counted from cells that exist, so they ship.
 *
 * The right-hand column is «Движения» alone for the same reason: turnover, dead
 * stock and stocktake are still owed by the backend (DESIGN-KIT §2.4).
 */

const MANAGE_INVENTORY = 'manage_inventory'

export interface Zone {
  id: string
  code: string
  name: string
  temp_c: string | null
  humidity_percent: string | null
  cell_count: number
  occupied_cells: number
  /** Null for a zone with no cells: nothing declared is not nothing stored. */
  fill_percent: string | null
  cells: Cell[]
}

interface CellMap {
  zones: Zone[]
  cells_total: number
  occupied_total: number
}

/**
 * A measured percentage, or the em dash the whole console uses for "not measured".
 *
 * One function so the tiles, the zone headers and the cells cannot each decide
 * differently what a null means.
 */
function percent(value: string | null): string | null {
  return value === null ? null : `${Number(value).toFixed(0)}%`
}

export function StorePage({ locale }: { locale: Locale }) {
  const { actor } = useSession()
  const t = useCallback((key: MessageKey) => translate(locale, key), [locale])

  const [map, setMap] = useState<CellMap | null>(null)
  const [movements, setMovements] = useState<Movement[]>([])
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)

  const mayManage = actor?.permissions.includes(MANAGE_INVENTORY) ?? false

  useChrome(
    map
      ? {
          meta: [
            { label: 'STORE.CELLS', value: String(map.cells_total) },
            { label: 'OCCUPIED', value: String(map.occupied_total) },
          ],
        }
      : null,
  )

  const load = useCallback(async () => {
    try {
      const [cells, feed] = await Promise.all([
        api.get<CellMap>('/store/cells'),
        api.get<Movement[]>('/store/movements'),
      ])
      setMap(cells)
      setMovements(feed)
      setError(null)
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
    }
  }, [locale])

  useEffect(() => {
    // The load is started from an async wrapper rather than called straight from
    // the effect body: `load` sets state, and a setState run synchronously inside
    // an effect is the cascading render `react-hooks/set-state-in-effect` rejects.
    void (async () => {
      await load()
    })()
  }, [load])

  /*
    The farm-wide fill, over the cells that exist rather than over a planned size.
    `null` while nothing has been declared at all — «0 %» there would say the
    warehouse is empty, when what is true is that nobody has described it yet.
  */
  const overall = useMemo(() => {
    if (!map || map.cells_total === 0) return null
    return `${((map.occupied_total * 100) / map.cells_total).toFixed(0)}%`
  }, [map])

  return (
    <section className="store">
      <header className="fleet__header">
        <h2>{t('store.title')}</h2>
      </header>

      {error && <p className="hv-hint hv-bad">{error}</p>}

      <div className="hv-grid hv-grid--4">
        <div className="hv-frame hv-kpi">
          <span className="hv-label">{t('store.cells')}</span>
          <span className="hv-kpi__v">{map ? map.cells_total : '—'}</span>
          <span className="hv-micro">
            {map
              ? translate(locale, 'store.occupied', {
                  occupied: String(map.occupied_total),
                  total: String(map.cells_total),
                })
              : t('common.loading')}
          </span>
        </div>
        <div className="hv-frame hv-kpi">
          <span className="hv-label">{t('store.fill')}</span>
          {/* An em dash rather than 0%: no cells declared is not an empty store. */}
          <span className="hv-kpi__v">{overall ?? t('common.none')}</span>
          <span className="hv-micro">{overall ? '' : t('store.fill.unknown')}</span>
        </div>
      </div>

      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('store.map')}</span>
          <span className="hv-panel__aside">{t('store.map.hint')}</span>
        </div>
        <div className="hv-panel__body hv-stack">
          {map?.zones.map((zone) => (
            <ZoneMap key={zone.id} zone={zone} locale={locale} onOpen={setOpen} />
          ))}
        </div>
        {mayManage && (
          <div className="hv-panel__foot">
            <DeclareForms locale={locale} onDone={load} />
          </div>
        )}
      </section>

      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>{t('store.movements')}</span>
        </div>
        <div className="hv-panel__body--none">
          <MovementTable rows={movements} locale={locale} />
        </div>
      </section>

      {open && (
        <CellDetail
          address={open}
          locale={locale}
          mayManage={mayManage}
          onClose={() => setOpen(null)}
          onChanged={load}
        />
      )}
    </section>
  )
}

function ZoneMap({
  zone,
  locale,
  onOpen,
}: {
  zone: Zone
  locale: Locale
  onOpen: (address: string) => void
}) {
  const conditions = [
    zone.temp_c === null ? null : `${Number(zone.temp_c).toFixed(0)} °C`,
    zone.humidity_percent === null ? null : `${Number(zone.humidity_percent).toFixed(0)}% RH`,
  ].filter((part): part is string => part !== null)

  return (
    <div className="hv-zone">
      <div className="hv-zone__head">
        <span>{[zone.code, zone.name, ...conditions].filter(Boolean).join(' · ')}</span>
        {/*
          A zone with no cells gets its own sentence rather than «0 %». The
          denominator is the cells that exist, so an undescribed zone has no
          denominator at all — and reporting zero would read as "nothing is
          stored here" (CLAUDE.md §1).
        */}
        <span>
          {zone.cell_count === 0
            ? translate(locale, 'store.zone.empty')
            : translate(locale, 'store.occupied', {
                occupied: String(zone.occupied_cells),
                total: String(zone.cell_count),
              })}
        </span>
      </div>
      <div className="hv-matrix">
        {zone.cells.map((cell) => (
          <CellNode key={cell.id} cell={cell} onOpen={onOpen} />
        ))}
      </div>
    </div>
  )
}

function CellNode({ cell, onOpen }: { cell: Cell; onOpen: (address: string) => void }) {
  const fill = percent(cell.fill_percent)
  const empty = cell.lot_count === 0
  return (
    <button
      type="button"
      // Every cell is a button, including the empty ones the kit draws as spans:
      // an empty cell is where somebody is about to put something, so it is the
      // one you most want to be able to open.
      className={empty ? 'hv-node hv-node--empty' : 'hv-node'}
      data-state={empty ? 'offline' : 'printing'}
      onClick={() => onOpen(cell.address)}
    >
      <span className="hv-node__id">{cell.address}</span>
      <span className="hv-node__pct">{fill ?? `${cell.lot_count}`}</span>
      {/*
        No bar at all when the capacity was never declared — the treatment
        `StatusWall` already gives a null progress. A bar at 0% would say the
        cell is empty about a cell holding four spools.
      */}
      {fill !== null && (
        <span className="hv-node__fill" style={{ '--p': fill } as React.CSSProperties} />
      )}
    </button>
  )
}

function MovementTable({ rows, locale }: { rows: Movement[]; locale: Locale }) {
  const t = (key: MessageKey) => translate(locale, key)
  if (rows.length === 0) return <p className="hv-hint">{t('store.movements.empty')}</p>
  return (
    <table className="hv-table">
      <thead>
        <tr>
          <th>{t('store.movement.time')}</th>
          <th>{t('store.movement.reason')}</th>
          <th>{t('store.movement.route')}</th>
          <th data-align="end">{t('store.movement.grams')}</th>
          <th>{t('store.movement.note')}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td className="hv-table__id">{new Date(row.at).toLocaleString(locale)}</td>
            <td>{translate(locale, `store.reason.${row.reason}` as MessageKey)}</td>
            <td className="hv-table__id">
              {[row.from_address, row.to_address].map((part) => part ?? '—').join(' → ')}
            </td>
            <td data-align="end">{Number(row.grams).toFixed(0)}</td>
            <td>{row.note ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

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
function DeclareForms({ locale, onDone }: { locale: Locale; onDone: () => Promise<void> }) {
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
