import { useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, translate, translateError, useSession } from '@printorian/ui'

import { Field } from './FleetAdmin'

/**
 * «Инвентаризация» and the «Расхождения» tile (design/store.html), over
 * `/store/stocktakes`.
 *
 * **Every figure here has the lines as its denominator.** «Проверено 12 из 14»
 * is counted lines over the lines the stocktake lined up when it opened — never
 * over a planned count or the whole store. A stocktake nobody has finished
 * counting shows exactly how far it got.
 *
 * **An uncounted line is a dash, never `0`.** The backend sends `counted_grams`
 * as null until somebody counts, and the panel draws it as absent; a zero there
 * would read as an empty shelf about a shelf nobody has reached (ADR-0007).
 *
 * **The money is asked for only behind `view_financials`**, from its own route,
 * the way `StoreMeasures` asks for dead stock: not requested, not drawn with
 * dashes. «Следующая» from the kit is not drawn at all — no setting says how
 * often the farm counts, and a date from an interval nobody chose is a promise
 * nobody made.
 */

const VIEW_FINANCIALS = 'view_financials'

/** `StocktakeSummary`, hand-mirrored (frontend/CLAUDE.md). */
export interface StocktakeSummary {
  id: string
  number: string
  status: 'open' | 'closed'
  zone_code: string | null
  opened_at: string
  closed_at: string | null
  positions: number
  counted: number
  matched: number
  short: number
  over: number
}

interface Line {
  lot_id: string
  label: string
  family: string
  cell_address: string | null
  expected_grams: string
  /** Null until counted. Absent, not zero. */
  counted_grams: string | null
  counted_at: string | null
  variance_grams: string | null
}

interface Detail extends StocktakeSummary {
  note: string | null
  lines: Line[]
}

interface Value {
  id: string
  number: string
  short_value: string
  over_value: string
  unpriced_lines: number
}

const INTL: Record<Locale, string> = { ru: 'ru-RU', en: 'en-GB' }

function money(value: string, locale: Locale): string {
  return `${Number(value).toLocaleString(INTL[locale], { maximumFractionDigits: 0 })} ₽`
}

/** The last *closed* count — an open one has not corrected anything yet. */
function lastClosed(history: StocktakeSummary[]): StocktakeSummary | null {
  return history.find((row) => row.status === 'closed') ?? null
}

/**
 * The KPI tile. The lines of the last closed count whose count differed from
 * the book, or a dash when the farm has never counted — not `0`, which would
 * say the book was checked and found right.
 */
export function StocktakeTile({
  history,
  locale,
}: {
  history: StocktakeSummary[]
  locale: Locale
}) {
  const last = lastClosed(history)
  return (
    <div className="hv-frame hv-kpi">
      <span className="hv-label">{translate(locale, 'store.variances')}</span>
      <span className="hv-kpi__v">{last ? last.short + last.over : '—'}</span>
      <span className="hv-micro">
        {last ? translate(locale, 'store.variances.hint') : translate(locale, 'store.stocktake.none')}
      </span>
    </div>
  )
}

export function StocktakePanel({
  history,
  locale,
  mayManage,
  onChanged,
}: {
  history: StocktakeSummary[]
  locale: Locale
  mayManage: boolean
  onChanged: () => Promise<void>
}) {
  const { actor } = useSession()
  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)
  const maySeeMoney = actor?.permissions.includes(VIEW_FINANCIALS) ?? false

  const latest = history[0] ?? null
  const open = latest?.status === 'open' ? latest : null
  const shown = open ?? lastClosed(history)

  const [detail, setDetail] = useState<Detail | null>(null)
  const [value, setValue] = useState<Value | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    // Both reads run from async wrappers rather than the effect body: a setState
    // run synchronously inside an effect is the cascading render
    // `react-hooks/set-state-in-effect` rejects, and the reset is a setState.
    void (async () => {
      if (!shown) {
        setDetail(null)
        return
      }
      try {
        setDetail(await api.get<Detail>(`/store/stocktakes/${shown.id}`))
      } catch {
        setDetail(null)
      }
    })()
    void (async () => {
      // The money read is not even started without the permission, and an open
      // count has corrected nothing yet, so it has no value to show.
      if (!shown || !maySeeMoney || shown.status !== 'closed') {
        setValue(null)
        return
      }
      try {
        setValue(await api.get<Value>(`/store/stocktakes/${shown.id}/value`))
      } catch {
        setValue(null)
      }
    })()
  }, [shown, maySeeMoney])

  const send = async (run: () => Promise<unknown>) => {
    try {
      await run()
      setFailed(null)
      await onChanged()
    } catch (exc: unknown) {
      setFailed(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
    }
  }

  // «Совпало» as a share of what was *counted*, not of what was lined up.
  const matchedShare =
    shown && shown.counted > 0 ? `${((shown.matched * 100) / shown.counted).toFixed(1)}%` : null

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('store.stocktake')}</span>
        <span className="hv-panel__aside">
          {open
            ? t('store.stocktake.open_now', { number: open.number })
            : shown?.closed_at
              ? t('store.stocktake.last', {
                  date: new Date(shown.closed_at).toLocaleDateString(locale),
                })
              : ''}
        </span>
      </div>
      <div className="hv-panel__body hv-panel__body--tight">
        {failed && <p className="hv-hint hv-bad">{failed}</p>}
        {!shown && <p className="hv-hint">{t('store.stocktake.none')}</p>}
        {shown && (
          <ul className="hv-leaders">
            <li className="hv-leader">
              <span className="hv-leader__k">{t('store.stocktake.checked')}</span>
              <span className="hv-leader__fill" />
              <span className="hv-leader__v">
                {shown.counted} / {shown.positions}
              </span>
            </li>
            <li className="hv-leader" data-tone="good">
              <span className="hv-leader__k">{t('store.stocktake.matched')}</span>
              <span className="hv-leader__fill" />
              <span className="hv-leader__v">
                {shown.matched}
                {matchedShare ? ` · ${matchedShare}` : ''}
              </span>
            </li>
            <li className="hv-leader" data-tone="warn">
              <span className="hv-leader__k">{t('store.stocktake.short')}</span>
              <span className="hv-leader__fill" />
              <span className="hv-leader__v">
                {shown.short}
                {value ? ` · ${money(value.short_value, locale)}` : ''}
              </span>
            </li>
            <li className="hv-leader">
              <span className="hv-leader__k">{t('store.stocktake.over')}</span>
              <span className="hv-leader__fill" />
              <span className="hv-leader__v">
                {shown.over}
                {value ? ` · ${money(value.over_value, locale)}` : ''}
              </span>
            </li>
          </ul>
        )}
        {value && value.unpriced_lines > 0 && (
          <p className="hv-micro">{t('store.stocktake.unpriced', { count: value.unpriced_lines })}</p>
        )}
        {open && detail && (
          <CountTable
            detail={detail}
            locale={locale}
            mayManage={mayManage}
            onCount={(lotId, grams) =>
              send(() =>
                api.post(`/store/stocktakes/${open.id}/lines/${lotId}`, { counted_grams: grams }),
              )
            }
          />
        )}
      </div>
      {mayManage && (
        <div className="hv-panel__foot">
          <span>{t('store.stocktake.foot')}</span>
          {open ? (
            <button
              className="hv-btn hv-btn--sm"
              type="button"
              onClick={() => void send(() => api.post(`/store/stocktakes/${open.id}/close`, {}))}
            >
              {t('store.stocktake.close')}
            </button>
          ) : (
            <StartForm locale={locale} onStart={(zone) => send(() => api.post('/store/stocktakes', zone))} />
          )}
        </div>
      )}
    </section>
  )
}

function CountTable({
  detail,
  locale,
  mayManage,
  onCount,
}: {
  detail: Detail
  locale: Locale
  mayManage: boolean
  onCount: (lotId: string, grams: string) => Promise<void>
}) {
  const t = (key: MessageKey) => translate(locale, key)
  return (
    <table className="hv-table">
      <thead>
        <tr>
          <th>{t('store.dead.lot')}</th>
          <th>{t('store.cell.address')}</th>
          <th data-align="end">{t('store.stocktake.expected')}</th>
          <th data-align="end">{t('store.stocktake.counted')}</th>
          {mayManage && <th />}
        </tr>
      </thead>
      <tbody>
        {detail.lines.map((line) => (
          <CountRow key={line.lot_id} line={line} mayManage={mayManage} onCount={onCount} />
        ))}
      </tbody>
    </table>
  )
}

function CountRow({
  line,
  mayManage,
  onCount,
}: {
  line: Line
  mayManage: boolean
  onCount: (lotId: string, grams: string) => Promise<void>
}) {
  const [grams, setGrams] = useState('')
  return (
    <tr>
      <td className="hv-table__id">{line.label}</td>
      <td className="hv-table__id">{line.cell_address ?? '—'}</td>
      <td data-align="end">{Number(line.expected_grams).toFixed(0)}</td>
      {/* Not counted yet: a dash. Zero is a count somebody made. */}
      <td data-align="end">
        {line.counted_grams === null ? '—' : Number(line.counted_grams).toFixed(0)}
      </td>
      {mayManage && (
        <td>
          <form
            className="hv-row"
            onSubmit={(event) => {
              event.preventDefault()
              void onCount(line.lot_id, grams).then(() => setGrams(''))
            }}
          >
            <input
              type="number"
              min={0}
              aria-label={line.label}
              value={grams}
              onChange={(event) => setGrams(event.target.value)}
              required
            />
            <button className="hv-btn hv-btn--sm" type="submit">
              ✓
            </button>
          </form>
        </td>
      )}
    </tr>
  )
}

function StartForm({
  locale,
  onStart,
}: {
  locale: Locale
  onStart: (body: { zone_code: string | null }) => Promise<void>
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const [zone, setZone] = useState('')
  return (
    <form
      className="hv-row"
      onSubmit={(event) => {
        event.preventDefault()
        // Blank is the whole store, sent as null rather than as an empty code.
        void onStart({ zone_code: zone === '' ? null : zone }).then(() => setZone(''))
      }}
    >
      <Field label={t('store.zone.code')} hint={t('store.stocktake.zone_hint')}>
        <input value={zone} onChange={(event) => setZone(event.target.value)} />
      </Field>
      <button className="hv-btn hv-btn--sm" type="submit">
        {t('store.stocktake.start')}
      </button>
    </form>
  )
}
