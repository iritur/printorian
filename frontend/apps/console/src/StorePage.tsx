import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, translate, translateError, useChrome, useSession } from '@printorian/ui'

import { ArticlesPanel } from './Articles'
import type { Article, ArticleMovement } from './Articles'
import { CellDetail } from './CellDetail'
// `Cell` and `Movement` are declared beside the window rather than here, so the
// dependency runs one way: the map imports the detail, and the detail imports
// nothing back. Both shapes appear in `GET /store/cells/{address}` as well as in
// the map, so either file could have owned them and only one ordering is acyclic.
import type { Cell, Movement } from './CellDetail'
// Split out at the seam: the map is a map, and declaring places is a form. It
// also keeps this file under the console's four-hundred-line convention.
import { DeclareForms } from './DeclareForms'
import { MovementFeed } from './MovementFeed'
import { StocktakePanel, StocktakeTile } from './Stocktake'
import type { StocktakeSummary } from './Stocktake'
import { StoreMeasures } from './StoreMeasures'

/**
 * The store (design/store.html): the cell map by zone, and the movement ledger.
 *
 * **Three KPI tiles, where the kit draws four, and the one that is missing is the
 * point.** «Стоимость остатков» is money, and only receiving writes
 * `MaterialLot.purchase_price` — so on a farm that has not received through it the
 * tile would read `0 ₽` over several hundred thousand roubles of filament, which is
 * an invented number in a nicer font (ADR-0007). `DiagnosticsPanel` made the same
 * call when it dropped «Версии» and «Журнал» rather than filling them with
 * placeholders. «Ячеек» and «Заполнение» are counted from cells that exist, and
 * «Расхождения» from the last closed stocktake's lines, so the three ship.
 *
 * Of the kit's right-hand column, «Движения» is drawn here, «Оборачиваемость»
 * and «Залежалое» by `StoreMeasures` beneath it, and «Инвентаризация» by
 * `StocktakePanel` (DESIGN-KIT §2.4).
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
  const [articles, setArticles] = useState<Article[]>([])
  const [articleMovements, setArticleMovements] = useState<ArticleMovement[]>([])
  const [stocktakes, setStocktakes] = useState<StocktakeSummary[]>([])
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
      const [cells, feed, counts, stocked, stockFeed] = await Promise.all([
        api.get<CellMap>('/store/cells'),
        api.get<Movement[]>('/store/movements'),
        api.get<StocktakeSummary[]>('/store/stocktakes'),
        api.get<Article[]>('/store/articles'),
        api.get<ArticleMovement[]>('/store/articles/movements'),
      ])
      setMap(cells)
      setMovements(feed)
      setStocktakes(counts)
      setArticles(stocked)
      setArticleMovements(stockFeed)
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
        <StocktakeTile history={stocktakes} locale={locale} />
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
          <MovementFeed spools={movements} articles={articleMovements} locale={locale} />
        </div>
      </section>

      {/* Packaging, consumables and spare parts — the shared stock (#35). */}
      <ArticlesPanel articles={articles} locale={locale} mayManage={mayManage} onChanged={load} />

      {/* Turnover for everybody; dead stock only asked for behind view_financials. */}
      <StoreMeasures locale={locale} />

      <StocktakePanel history={stocktakes} locale={locale} mayManage={mayManage} onChanged={load} />

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
  // Spools and articles both occupy a cell (#35); either makes it not empty.
  const held = cell.lot_count + cell.article_count
  const empty = held === 0
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
      <span className="hv-node__pct">{fill ?? `${held}`}</span>
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
