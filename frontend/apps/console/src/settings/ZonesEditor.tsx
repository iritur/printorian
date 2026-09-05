import { useEffect, useState } from 'react'

import type { Locale, MessageKey } from '@printorian/ui'
import { translate } from '@printorian/ui'

/**
 * The shipping zone tariff (design/logistics.html, «Зоны и тарифы»).
 *
 * Its own file rather than a third editor inside `SettingsPage.tsx`, which is
 * already 1,104 lines. Everything here is Harvester — `hv-table`, `hv-table__id`,
 * `hv-unit` — copied from the ladder and tier editors rather than newly styled.
 *
 * The kit draws five columns and this draws four of them. «Отправлений» is a
 * count of parcels sent through a zone, and nothing counts parcels yet: rendering
 * it as `0` would say the farm shipped nothing to Moscow, which is a measurement
 * it never took (ADR-0007). It returns with the shipment record.
 *
 * The postcode prefixes are not in the kit's table but in its footer — «ЗОНА
 * ОПРЕДЕЛЯЕТСЯ ПО ИНДЕКСУ» — and they are what actually decides a price, so they
 * are editable here rather than being a rule with no visible cause.
 */

/** One row, in exactly the shape `GET/PUT /settings/logistics.zones` carries. */
export interface ZoneRow {
  code: string
  /** Money as a string, all the way to the wire — a JSON number is a float. */
  base: string
  per_kg: string
  transit_days: number
  postcode_prefixes: string[]
  enabled: boolean
}

export function ZonesEditor(props: {
  locale: Locale
  rows: ZoneRow[]
  dirty: boolean
  onChange: (rows: ZoneRow[]) => void
  onRevert: () => void
  name: string
  hint: string
  revertLabel: string
}) {
  const t = (key: MessageKey) => translate(props.locale, key)
  const { rows } = props

  const update = (index: number, patch: Partial<ZoneRow>) =>
    props.onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  const remove = (index: number) => props.onChange(rows.filter((_, i) => i !== index))

  const add = () =>
    props.onChange([
      ...rows,
      { code: '', base: '0', per_kg: '0', transit_days: 1, postcode_prefixes: [], enabled: true },
    ])

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
              <th>{t('settings.zones.code')}</th>
              <th>{t('settings.zones.prefixes')}</th>
              <th data-align="end">{t('settings.zones.base')}</th>
              <th data-align="end">{t('settings.zones.per_kg')}</th>
              <th>{t('settings.zones.transit')}</th>
              <th>{t('settings.zones.enabled')}</th>
              <th />
            </tr>
          </thead>
          {/* A switched-off zone is greyed rather than hidden, as the kit draws
              it: the farm has drawn it and is not serving it, and an order priced
              against it before it was retired still reads back. */}
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} className={row.enabled ? undefined : 'hv-faint'}>
                <td className="hv-table__id">
                  <input
                    type="text"
                    aria-label={`${t('settings.zones.code')} ${index + 1}`}
                    value={row.code}
                    onChange={(event) => update(index, { code: event.target.value })}
                  />
                </td>
                <td>
                  <PrefixInput
                    label={`${t('settings.zones.prefixes')} ${index + 1}`}
                    prefixes={row.postcode_prefixes}
                    onChange={(postcode_prefixes) => update(index, { postcode_prefixes })}
                  />
                </td>
                <td data-align="end">
                  <span className="hv-unit">
                    <input
                      type="number"
                      min="0"
                      step="1"
                      aria-label={`${t('settings.zones.base')} ${index + 1}`}
                      value={row.base}
                      onChange={(event) => update(index, { base: event.target.value })}
                    />
                    <span className="hv-unit__u">{t('settings.unit.rub')}</span>
                  </span>
                </td>
                <td data-align="end">
                  <span className="hv-unit">
                    <input
                      type="number"
                      min="0"
                      step="1"
                      aria-label={`${t('settings.zones.per_kg')} ${index + 1}`}
                      value={row.per_kg}
                      onChange={(event) => update(index, { per_kg: event.target.value })}
                    />
                    <span className="hv-unit__u">{t('settings.unit.rub_per_kg')}</span>
                  </span>
                </td>
                <td>
                  <span className="hv-unit">
                    <input
                      type="number"
                      min="0"
                      step="1"
                      aria-label={`${t('settings.zones.transit')} ${index + 1}`}
                      value={row.transit_days}
                      onChange={(event) =>
                        update(index, { transit_days: Number(event.target.value) })
                      }
                    />
                    <span className="hv-unit__u">{t('settings.unit.days')}</span>
                  </span>
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`${t('settings.zones.enabled')} ${index + 1}`}
                    checked={row.enabled}
                    onChange={(event) => update(index, { enabled: event.target.checked })}
                  />
                </td>
                <td>
                  <button
                    className="hv-btn hv-btn--sm hv-btn--danger"
                    type="button"
                    onClick={() => remove(index)}
                  >
                    {t('settings.zones.remove')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="hv-panel__foot">
        <span>{t('settings.zones.by_postcode')}</span>
        <button className="hv-btn hv-btn--sm" type="button" onClick={add}>
          {t('settings.zones.add')}
        </button>
      </div>
    </section>
  )
}

/**
 * The prefix list, edited as one comma-separated field.
 *
 * It holds its own text rather than deriving the input's value from the parsed
 * array, and that is the whole reason it is a component. Rendering
 * `prefixes.join(', ')` back into a controlled input means the separator vanishes
 * the instant it is typed — the parse drops the empty segment after it — so the
 * list can never be extended by anyone who types left to right.
 *
 * Empty segments are dropped on the way out, so a trailing comma is a
 * half-finished edit rather than a blank prefix. A blank prefix would match every
 * postcode on earth, and the backend refuses it for that reason; there is no need
 * to make the owner discover that by pressing save.
 */
function PrefixInput(props: {
  label: string
  prefixes: string[]
  onChange: (prefixes: string[]) => void
}) {
  const [text, setText] = useState(props.prefixes.join(', '))

  // Re-sync when the row is reverted or reloaded from the server. Compared
  // against this field's *own* parse rather than against the raw text, so an edit
  // in progress ("101, ") is left alone while a genuine change from outside is
  // not — and because the two agree immediately after every keystroke, listing
  // `text` as a dependency costs nothing and keeps the list honest.
  useEffect(() => {
    if (parsePrefixes(text).join(' ') !== props.prefixes.join(' ')) {
      setText(props.prefixes.join(', '))
    }
  }, [props.prefixes, text])

  return (
    <input
      type="text"
      aria-label={props.label}
      value={text}
      onChange={(event) => {
        setText(event.target.value)
        props.onChange(parsePrefixes(event.target.value))
      }}
    />
  )
}

function parsePrefixes(text: string): string[] {
  return text
    .split(',')
    .map((part) => part.trim())
    .filter((part) => part.length > 0)
}
