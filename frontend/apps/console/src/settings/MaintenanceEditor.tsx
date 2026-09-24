/**
 * «Периодичность по умолчанию» — the maintenance table on settings section 07.
 *
 * The rows are `service.maintenance_defaults`: one per kind of service operation
 * the fleet knows, with the printing hours between occurrences. It is what a new
 * machine's service card is seeded from (`POST /printers`) and what an operation
 * added without a periodicity takes — the kit's «ПРИМЕНЯЕТСЯ К НОВЫМ МАШИНАМ».
 *
 * The code set is closed on the server (`error.fleet.maintenance_kind_unknown`),
 * so as in `OperationsEditor` there is no «Добавить» control. A row *can* be
 * removed, though, and that is a real edit rather than a gap: a kind the table
 * does not carry is not seeded on a new machine, which is how a farm without an
 * AMS stops giving every printer a filter change it will never do. Removed rows
 * can be put back with «Вернуть».
 *
 * **Two of the kit's five columns are deliberately absent.** «Простой» and
 * «Расход» have no reader anywhere — `ServiceOperation` carries neither, and
 * nothing prices a service — and a settings column nothing reads is a number the
 * owner set and the farm ignored (CLAUDE.md §1). They arrive with whatever reads
 * them.
 */

/** One row, exactly as the API sends and accepts it. */
export interface MaintenanceRow {
  code: string
  interval_hours: number
}

export function MaintenanceEditor(props: {
  /** Structural rather than `SettingView`, for the reason `OperationsEditor` gives. */
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
  intervalLabel: string
  hoursUnit: string
  removeLabel: string
  restoreLabel: string
  /** Every kind the fleet knows, in the order the kit lists them. */
  kinds: readonly string[]
  rowLabel: (code: string) => string
}) {
  const rows =
    ((props.draft !== undefined ? props.draft : props.field.value) as MaintenanceRow[] | null) ?? []
  const present = new Map(rows.map((row) => [row.code, row]))

  const update = (code: string, interval_hours: number) =>
    props.onChange(rows.map((row) => (row.code === code ? { ...row, interval_hours } : row)))
  const remove = (code: string) => props.onChange(rows.filter((row) => row.code !== code))
  // Restored at the end rather than at its original position: the server keys
  // rows by code, so order carries no meaning, and the kit's order is what the
  // table below draws whatever the array says.
  const restore = (code: string) => props.onChange([...rows, { code, interval_hours: 500 }])

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
              <th>{props.intervalLabel}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {props.kinds.map((code) => {
              const row = present.get(code)
              return (
                <tr key={code} data-seeded={row !== undefined}>
                  <td>{props.rowLabel(code)}</td>
                  <td className="hv-table__id">{code}</td>
                  <td>
                    {row ? (
                      <span className="hv-unit">
                        <input
                          type="number"
                          min="1"
                          step="10"
                          aria-label={`${props.intervalLabel} ${code}`}
                          value={row.interval_hours}
                          onChange={(event) => update(code, Number(event.target.value))}
                        />
                        <span className="hv-unit__u">{props.hoursUnit}</span>
                      </span>
                    ) : (
                      // Not seeded: a new machine gets no such row. An em dash,
                      // because there is no interval — not «0 ч».
                      <span className="hv-micro">—</span>
                    )}
                  </td>
                  <td data-align="end">
                    {row ? (
                      <button
                        className="hv-btn hv-btn--sm"
                        type="button"
                        aria-label={`${props.removeLabel} ${code}`}
                        onClick={() => remove(code)}
                      >
                        {props.removeLabel}
                      </button>
                    ) : (
                      <button
                        className="hv-btn hv-btn--sm"
                        type="button"
                        aria-label={`${props.restoreLabel} ${code}`}
                        onClick={() => restore(code)}
                      >
                        {props.restoreLabel}
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
