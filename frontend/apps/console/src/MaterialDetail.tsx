import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import {
  Modal,
  api,
  formatLocation,
  formatMoney,
  translate,
  translateError,
  useSession,
} from '@printorian/ui'

import { Field } from './FleetAdmin'

/**
 * The material's own window, read from `GET /materials/{code}`.
 *
 * The popup used to render the row the table already had, which left the detail
 * route with no caller at all (DESIGN-KIT §4, issue #38) and cost the screen the
 * half of a material that is not in the table: density, tensile strength,
 * heat-deflection temperature, the flexible and outdoor flags, and the purchase
 * price. `MaterialsPage`'s row interface never declared those fields, so the
 * window could say where a spool is and never what the plastic *is* — which is
 * the question somebody opens a material to answer.
 *
 * Fetching also fixes what a row could only approximate. `InventoryService.table`
 * filters on `is_active` and `get_by_code` does not, so a retired spec is
 * readable here and simply absent there; and the response is read at the moment
 * the window opens rather than whenever the table last loaded.
 */

const MANAGE_INVENTORY = 'manage_inventory'
/**
 * Spelled out here rather than imported from `PriceReview`. The constant *is* the
 * permission's name as the backend spells it, and a screen borrowing another
 * screen's copy is how a rename ends up half-applied with no type error.
 */
const VIEW_FINANCIALS = 'view_financials'

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

/**
 * `MaterialSpecView`, as `GET /materials/{code}` serves it.
 *
 * Hand-declared, which is this console's convention — the generated client covers
 * the transport and each screen types the rows it reads (`frontend/CLAUDE.md`).
 * The three nullable numbers are nullable in the database too: a spec whose
 * tensile figure nobody has entered has no tensile figure, and the distance
 * between that and zero is the whole of ADR-0007.
 */
export interface MaterialSpec {
  id: string
  code: string
  name: string
  family: string
  color_name: string
  color_hex: string
  density_g_per_cm3: string
  sell_price_per_gram: string
  purchase_price_per_1000m: string | null
  tensile_mpa: string | null
  hdt_c: string | null
  is_flexible: boolean
  is_outdoor_safe: boolean
  status: string
  total_remaining_grams: string
  lot_count: number
  lots: MaterialLot[]
}

export function MaterialDetail({
  code,
  locale,
  printerNames,
  onClose,
  onChanged,
}: {
  code: string
  locale: Locale
  printerNames: Record<string, string>
  onClose: () => void
  /** The table behind this window reads the same lots; a lot moved here moves there. */
  onChanged: () => void
}) {
  const { actor } = useSession()
  const t = useCallback((key: MessageKey) => translate(locale, key), [locale])

  const mayManage = actor?.permissions.includes(MANAGE_INVENTORY) ?? false
  const seesMoney = actor?.permissions.includes(VIEW_FINANCIALS) ?? false

  const [spec, setSpec] = useState<MaterialSpec | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    // A response for a window that has since been closed is dropped rather than
    // written into the state of a component nobody is looking at.
    let current = true
    api
      .get<MaterialSpec>(`/materials/${encodeURIComponent(code)}`)
      .then((found) => {
        if (!current) return
        setSpec(found)
        setError(null)
      })
      .catch((exc: unknown) => {
        if (!current) return
        // The figures go with the failure. Last-good numbers under an error
        // banner are the shape that reads as measured — a person sees a table
        // and a warning and believes the table. An unknown code answers 404
        // here (`error.inventory.spec_not_found`), and ADR-0007 is explicit that
        // it has to render as that sentence rather than as a window of zeros.
        setSpec(null)
        setError(
          exc instanceof ApiError
            ? translateError(locale, { code: exc.code, details: exc.details })
            : translate(locale, 'error.internal'),
        )
      })
    return () => {
      current = false
    }
  }, [code, locale, reloads])

  // Adding a lot or taking a spool off a printer changes this material and the
  // row behind it, so both are re-read. The window stays open and updates, which
  // is what a window implies; closing it to show a change would lose whatever
  // else was being looked at.
  const refresh = useCallback(() => {
    setReloads((count) => count + 1)
    onChanged()
  }, [onChanged])

  return (
    <Modal
      wide
      title={`${code} :: ${t('materials.title')}`}
      // The chrome strip carries what the response says, so it carries nothing
      // until there is a response. Spread rather than passed as `undefined`
      // because `exactOptionalPropertyTypes` makes those two different things.
      {...(spec
        ? {
            meta: [
              { label: 'СЕМЕЙСТВО', value: spec.family },
              { label: 'ЦВЕТ', value: spec.color_name || '—' },
              { label: 'LOTS', value: String(spec.lot_count) },
            ],
            status: translate(locale, `material.status.${spec.status}` as MessageKey),
          }
        : {})}
      path={`/INVENTORY/MATERIALS/${code.toUpperCase()}`}
      onClose={onClose}
      footer={
        <>
          <span>{spec?.name ?? code}</span>
          <button className="hv-btn hv-btn--sm" type="button" onClick={onClose}>
            {t('common.close')}
          </button>
        </>
      }
    >
      {error ? (
        <p className="hv-hint hv-bad" role="alert">
          {error}
        </p>
      ) : !spec ? (
        <p className="admin-detail__muted">{t('common.loading')}</p>
      ) : (
        <>
          <dl className="admin-detail__facts">
            <dt>{t('materials.family')}</dt>
            <dd>{spec.family}</dd>
            <dt>{t('materials.stock')}</dt>
            <dd>{Number(spec.total_remaining_grams).toFixed(0)}</dd>
            <dt>{t('materials.price')}</dt>
            <dd>
              {formatMoney((Number(spec.sell_price_per_gram) * 1000).toFixed(2), 'RUB', locale)}
            </dd>
            {/* What the farm paid, which is not what the customer is charged.
                `VIEW_FINANCIALS` is deliberately implied by no production
                permission (root CLAUDE.md §1), so an engineer opening a material
                to find a spool does not read the farm's buying price on the way
                past. The route serves the field to anyone who may read materials
                at all — this is the console declining to put it on a screen, and
                it is not a substitute for the route deciding. */}
            {seesMoney && (
              <>
                <dt>{t('materials.purchase_price')}</dt>
                <dd>
                  {spec.purchase_price_per_1000m === null
                    ? t('common.none')
                    : formatMoney(spec.purchase_price_per_1000m, 'RUB', locale)}
                </dd>
              </>
            )}
          </dl>

          {/* The kit's «Свойства» panel, and the reason this window fetches at
              all: not one of these five reaches the table. */}
          <h4>{t('materials.properties')}</h4>
          <ul className="hv-leaders">
            <Leader
              label={t('materials.density')}
              value={Number(spec.density_g_per_cm3).toFixed(2)}
            />
            {/* Null is "nobody has entered it", and an em dash is how this console
                says so. `Number(null)` is 0, which would present a plastic whose
                strength was never recorded as one that shatters. */}
            <Leader
              label={t('materials.tensile')}
              value={spec.tensile_mpa === null ? t('common.none') : Number(spec.tensile_mpa).toFixed(0)}
            />
            <Leader
              label={t('materials.hdt')}
              value={spec.hdt_c === null ? t('common.none') : Number(spec.hdt_c).toFixed(0)}
            />
            {/* Declarations on the spec rather than readings, so `false` is an
                answer and these two say "нет" where the three above say "—". */}
            <Leader
              label={t('materials.flexible')}
              value={spec.is_flexible ? t('common.yes') : t('common.no')}
            />
            <Leader
              label={t('materials.outdoor')}
              value={spec.is_outdoor_safe ? t('common.yes') : t('common.no')}
            />
          </ul>

          {/* Per-lot, because the table's location column collapses duplicates:
              this is where someone finds which spool to physically fetch. */}
          <h4>{t('materials.lots')}</h4>
          {spec.lots.length === 0 ? (
            <p className="admin-detail__muted">{t('common.empty')}</p>
          ) : (
            <ul className="admin-detail__list">
              {spec.lots.map((lot) => (
                <li key={lot.id}>
                  <span>{lot.label || lot.id.slice(0, 8)}</span>
                  <span className="admin-detail__muted">
                    {formatLocation(lot, locale, printerNames)}
                  </span>
                  <span>{Number(lot.remaining_grams).toFixed(0)} г</span>
                  {/* Only for a spool actually in a machine — there is nothing
                      to remove from a shelf. */}
                  {mayManage && lot.location_kind === 'printer' && lot.printer_id && (
                    <UnmountButton lot={lot} locale={locale} onDone={refresh} />
                  )}
                </li>
              ))}
            </ul>
          )}

          {mayManage && <LotForm material={spec} locale={locale} onDone={refresh} />}
        </>
      )}
    </Modal>
  )
}

/** One row of the kit's `hv-leaders` list: key, dotted fill, value. */
function Leader({ label, value }: { label: string; value: string }) {
  return (
    <li className="hv-leader">
      <span className="hv-leader__k">{label}</span>
      <i className="hv-leader__fill" />
      <span className="hv-leader__v">{value}</span>
    </li>
  )
}

function UnmountButton({
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

function LotForm({
  material,
  locale,
  onDone,
}: {
  material: MaterialSpec
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
            spec_code: material.code,
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
