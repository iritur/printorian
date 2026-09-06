/**
 * «Каталог операций» — the postprocessing table on settings section 08.
 *
 * The rows are `postprocess.operations`, and the code set is **closed**: the
 * server refuses a body that adds or drops a code, because `apps/web/src/config.ts`
 * offers exactly `raw`, `sanded`, `primed`, `painted` and a fifth row here would be
 * a finish the farm had priced and the storefront never offered. So there is no
 * «Добавить операцию» button and no remove control — the kit draws one, and porting
 * it would draw an action the API answers with `error.settings.finish_code_unknown`.
 * `TiersEditor`'s "code fixed, values editable" is the shape this follows.
 *
 * `extra_days` is carried through every edit untouched. No column draws it and
 * nothing reads it yet, but a row rebuilt without it saves «Окраска» at zero extra
 * days — a value the owner never touched, changed by editing a norm-hour.
 *
 * **Two of the kit's columns are deliberately absent**, each for a measured reason
 * rather than for want of time. «На см² поверхности» needs a surface area inside
 * `PriceSpec`, and surface area does not reach the checkout: the quote context
 * carries `volume_cm3` and `bounding_box_mm` and no `surface_area_mm2`, and
 * `CheckoutPage` sends no mesh at all — so the term would price at the configurator
 * and not on the order, and pricing an unmeasured mesh at 0 cm² is a claim the farm
 * never made (ADR-0007). «Доступна» is only honest once the configurator stops
 * offering what the farm turned off, and `FinishStep` renders a hardcoded list
 * before any quote, so it needs a public read of the catalogue — a new endpoint and
 * a regenerated client. Both are the named follow-up, and `docs/DESIGN-KIT.md` §2.1
 * says so where the next reader will look.
 *
 * Lives in its own file rather than beside `TiersEditor` in `SettingsPage.tsx`
 * because that file is already past a thousand lines; the two existing editors are
 * left where they are, since moving them is churn this change does not need.
 */

/** One row of the catalogue, exactly as the API sends and accepts it. */
export interface OperationRow {
  code: string
  labor_hours: string
  flat_fee: string
  extra_days: number
}

export function OperationsEditor(props: {
  /**
   * Structural rather than `SettingView`, which is declared in `SettingsPage.tsx`:
   * importing it back would make the pair circular for one property this reads.
   */
  field: { value: unknown }
  draft: unknown
  onChange: (value: unknown) => void
  onRevert: () => void
  dirty: boolean
  name: string
  hint: string
  revertLabel: string
  operationLabel: string
  codeLabel: string
  hoursLabel: string
  feeLabel: string
  hoursUnit: string
  feeUnit: string
  rowLabel: (code: string) => string
}) {
  const rows =
    ((props.draft !== undefined ? props.draft : props.field.value) as OperationRow[] | null) ?? []

  const update = (index: number, patch: Partial<OperationRow>) =>
    props.onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  return (
    <section className="hv-panel" data-changed={props.dirty}>
      <div className="hv-panel__head">
        <span>{props.name}</span>
        <span className="hv-panel__aside">
          {props.dirty && (
            <button className="hv-btn hv-btn--sm" type="button" onClick={props.onRevert}>
              {props.revertLabel}
            </button>
          )}
        </span>
      </div>
      {props.hint && (
        <p className="hv-micro" style={{ padding: 'var(--hv-3)' }}>
          {props.hint}
        </p>
      )}
      <div className="hv-panel__body--none">
        <table className="hv-table">
          <thead>
            <tr>
              <th>{props.operationLabel}</th>
              <th>{props.codeLabel}</th>
              <th>{props.hoursLabel}</th>
              <th>{props.feeLabel}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={row.code}>
                <td>{props.rowLabel(row.code)}</td>
                <td className="hv-table__id">{`postprocess.${row.code}`}</td>
                <td>
                  {/*
                    Every row gets a real input, including «Без обработки», where
                    the kit draws an em dash. A dash on this screen means "not
                    measured"; nought norm-hours for an unfinished part is a
                    number the farm does know, and drawing it as unknown is the
                    same invention in the other direction (ADR-0007).
                  */}
                  <span className="hv-unit">
                    <input
                      type="number"
                      min="0"
                      step="0.1"
                      aria-label={`${props.hoursLabel} ${row.code}`}
                      value={row.labor_hours}
                      onChange={(event) => update(index, { labor_hours: event.target.value })}
                    />
                    <span className="hv-unit__u">{props.hoursUnit}</span>
                  </span>
                </td>
                <td>
                  <span className="hv-unit">
                    <input
                      type="number"
                      min="0"
                      step="10"
                      aria-label={`${props.feeLabel} ${row.code}`}
                      value={row.flat_fee}
                      onChange={(event) => update(index, { flat_fee: event.target.value })}
                    />
                    <span className="hv-unit__u">{props.feeUnit}</span>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
