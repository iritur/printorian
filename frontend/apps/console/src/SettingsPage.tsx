import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import {
  DEFAULT_LOCALE,
  TabRail,
  TabView,
  api,
  catalogues,
  translate,
  translateError,
  useChrome,
  useSession,
} from '@printorian/ui'

/**
 * The farm's own settings (design/settings.html).
 *
 * Backed by the `contexts/settings` store: a key/value table with an audit. The
 * server sends the catalogue sectioned, and this screen renders one control per
 * `kind` — number/unit, select, switch, string, and a write-only secret that is
 * never read back. Editing marks a row dirty and counts into the save bar; saving
 * PUTs each dirty key, so every change is a separate audited «было · стало».
 *
 * Owner-only: the nav gates the whole screen on `manage_settings`, and the API
 * enforces it again, so hiding the tab never weakens anything.
 */

const MANAGE_SETTINGS = 'manage_settings'

interface SettingView {
  key: string
  section: string
  kind: string
  value: unknown
  default: unknown
  is_overridden: boolean
  is_set: boolean
  options: string[]
  group: string | null
}

interface SectionView {
  id: string
  fields: SettingView[]
}

interface SettingChange {
  key: string
  old_value: unknown
  new_value: unknown
  changed_at: string
  changed_by_name: string | null
}

interface LadderRung {
  min_quantity: number
  percent: string
}

interface TierRow {
  code: string
  discount_percent: string
  margin_percent_override: string | null
}

/** Equal for dirty-tracking: arrays compare as JSON, scalars as strings. */
function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    return JSON.stringify(a) === JSON.stringify(b)
  }
  return String(a) === String(b)
}

//: The unit each numeric field draws beside its number. The unit *label* is
//: localised (`settings.unit.<code>`); this is the field → code mapping the kit
//: fixes in its HTML. A field absent here draws a bare input.
const UNITS: Record<string, string> = {
  'general.farm_open_hour': 'clock_hour',
  'general.farm_close_hour': 'clock_hour',
  'pricing.labor_rate_per_hour': 'rub_per_hour',
  'pricing.labor_hours_per_print_hour': 'hours_per_hour',
  'pricing.labor_hours_per_job': 'hour',
  'pricing.engineering_hours_per_resize': 'hour',
  'pricing.postprocess_rate_per_hour': 'rub_per_hour',
  'pricing.electricity_rate_per_kwh': 'rub_per_kwh',
  'pricing.printer_power_kw': 'kw',
  'pricing.depreciation_per_printer_hour': 'rub_per_hour',
  'pricing.material_procurement_flat': 'rub',
  'pricing.multicolor_purge_grams_per_extra_color': 'grams',
  'pricing.overhead_per_print_hour': 'rub_per_hour',
  'pricing.failure_buffer_percent': 'percent',
  'pricing.rush_surcharge_percent': 'percent',
  'pricing.margin_percent': 'percent',
  'pricing.packaging_per_unit': 'rub',
  'pricing.shipping_flat': 'rub',
  'scheduling.due_soon_hours': 'hour',
  'scheduling.load_horizon_minutes': 'minutes',
  'scheduling.expensive_per_hour': 'rub_per_hour',
  'scheduling.comfortable_headroom': 'multiplier',
  'scheduling.scheduler_tick_seconds': 'seconds',
  'sla.promise_buffer_percent': 'percent',
  'sla.min_lead_hours': 'hour',
  'sla.rush_lead_hours': 'hour',
  'sla.percent_per_day': 'percent',
  'sla.max_percent': 'percent',
  'sla.sla_sweep_seconds': 'seconds',
  'sla.price_variance_tolerance': 'percent',
  'inventory.low_stock_grams': 'grams',
  'inventory.critical_stock_grams': 'grams',
  'inventory.default_lead_days': 'days',
  'inventory.drying_valid_hours': 'hour',
  'inventory.writeoff_below_grams': 'grams',
  'service.telemetry_poll_seconds': 'seconds',
  'service.driver_timeout_seconds': 'seconds',
  'service.driver_send_retries': 'times',
  //: The kit draws these two as one row with «с»/«до» between the boxes; the
  //: catalogue has them as two keys, so they are two rows and each carries the
  //: hour unit — the only numeric fields that were drawing bare.
  'notify.quiet_hours_from': 'clock_hour',
  'notify.quiet_hours_to': 'clock_hour',
  'logistics.volumetric_divisor': 'cm3_per_kg',
  'logistics.free_shipping_threshold': 'rub',
  'finance.vat_percent': 'percent',
  'finance.prepayment_percent': 'percent',
  'finance.invoice_due_days': 'work_days',
  'finance.refund_before_print_percent': 'percent',
  'finance.refund_after_print_percent': 'percent',
  'finance.refund_approval_threshold': 'rub',
  'security.session_ttl_hours': 'hour',
  'security.password_min_length': 'symbols',
  'security.lockout_attempts': 'attempts',
  'security.audit_retention_days': 'days',
  'integrations.slicer_timeout_seconds': 'seconds',
  'maintenance.backup_hour': 'clock_hour',
  'maintenance.backup_retention': 'pieces',
  'maintenance.model_retention_days': 'days',
  'maintenance.telemetry_retention_days': 'days',
}

//: The scheduler weights draw as 0–10 range sliders, not number boxes.
const isWeight = (key: string) => key.startsWith('scheduling.weight_')

export function SettingsPage({ locale }: { locale: Locale }) {
  const { actor, ready } = useSession()
  const [sections, setSections] = useState<SectionView[] | null>(null)
  const [active, setActive] = useState('general')
  const [drafts, setDrafts] = useState<Record<string, unknown>>({})
  const [history, setHistory] = useState<SettingChange[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const entitled = actor?.permissions.includes(MANAGE_SETTINGS) ?? false

  const t = (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details)
  const cat = (catalogues[locale] ?? catalogues[DEFAULT_LOCALE]) as Record<string, string>
  const text = (key: string) => cat[key] ?? ''
  const fieldName = (key: string) => text(`settings.field.${key}`)
  const fieldHint = (key: string) => text(`settings.field.${key}.hint`)
  const optionLabel = (key: string, value: string) => text(`settings.option.${key}.${value}`) || value

  useChrome(
    sections
      ? {
          meta: [
            { label: 'SCOPE', value: 'FARM.CONFIG' },
            { label: 'ПРАВА', value: 'MANAGE_SETTINGS' },
          ],
          path: '/SYSTEM/SETTINGS/FARM.CONFIG',
        }
      : null,
  )

  const load = useCallback(async () => {
    try {
      const [nextSections, nextHistory] = await Promise.all([
        api.get<SectionView[]>('/settings/sections'),
        api.get<SettingChange[]>('/settings/history'),
      ])
      setSections(nextSections)
      setHistory(nextHistory)
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
    if (!entitled) return
    // The await is what makes the state update provably asynchronous — see
    // `packages/ui/src/effects.ts` for why every fetch-on-mount is shaped so.
    void (async () => {
      await load()
    })()
  }, [entitled, load])

  // A field is dirty when a draft exists and differs from the committed value.
  const dirtyKeys = useMemo(() => {
    const all: SettingView[] = (sections ?? []).flatMap((section) => section.fields)
    return all.filter((field) => {
      if (!(field.key in drafts)) return false
      return !sameValue(drafts[field.key], field.value)
    })
  }, [sections, drafts])

  const setDraft = (key: string, value: unknown) =>
    setDrafts((current) => ({ ...current, [key]: value }))

  const revert = (key: string) =>
    setDrafts((current) => {
      const next = { ...current }
      delete next[key]
      return next
    })

  //: The save bar's «Отмена» — every pending edit at once, where `revert` drops
  //: one row. Nothing has been written yet, so this only forgets the drafts; the
  //: committed values are already what the screen falls back to.
  const discard = () => setDrafts({})

  const save = useCallback(async () => {
    if (dirtyKeys.length === 0) return

    // Refuse an empty number box rather than coerce it. `Number('')` is `0`, not
    // `NaN`, so a guard on NaN never fired for the case it was written for and a
    // cleared rate saved as free — the farm quoting at cost until someone
    // noticed. Nothing is written when one field is unusable: a partial save
    // would leave the owner reading a screen that is half their edit.
    const unusable = dirtyKeys.find((field) => {
      if (field.kind !== 'integer' && field.kind !== 'decimal') return false
      const raw = String(drafts[field.key] ?? '').trim()
      return raw === '' || !Number.isFinite(Number(raw))
    })
    if (unusable) {
      // The catalogue is read here rather than through `fieldName` so that
      // `locale` stays this callback's only dependency on the render scope.
      const labels = (catalogues[locale] ?? catalogues[DEFAULT_LOCALE]) as Record<string, string>
      const field = labels[`settings.field.${unusable.key}`] ?? unusable.key
      setError(translate(locale, 'settings.error.blank', { field }))
      return
    }

    setSaving(true)
    setError(null)
    try {
      // Sequential rather than parallel: each key audits itself, and a failure
      // mid-way should leave the ones already saved intact and visible.
      for (const field of dirtyKeys) {
        const raw = drafts[field.key]
        let value: unknown = raw
        if (field.kind === 'integer') value = Number(raw)
        if (field.kind === 'secret' && !String(raw).trim()) continue
        await api.put(`/settings/${field.key}`, { value })
      }
      setDrafts({})
      await load()
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
      // Re-read so the screen shows what actually landed, not what we hoped.
      await load()
    } finally {
      setSaving(false)
    }
  }, [dirtyKeys, drafts, load, locale])

  if (ready && !entitled) return <p className="notice">{t('fleet.forbidden')}</p>

  const tabs = (sections ?? []).map((section) => ({
    key: section.id,
    label: t(`settings.section.${section.id}` as MessageKey),
  }))
  const current = sections?.some((section) => section.id === active) ? active : tabs[0]?.key
  const farmNameValue = String(
    (sections ?? [])
      .flatMap((section) => section.fields)
      .find((field) => field.key === 'general.farm_name')?.value ?? '',
  )

  const renderField = (field: SettingView) => {
    if (field.key === 'pricing.tiers') {
      return (
        <TiersEditor
          key={field.key}
          field={field}
          locale={locale}
          draft={drafts[field.key]}
          onChange={(value) => setDraft(field.key, value)}
          onRevert={() => revert(field.key)}
          name={fieldName(field.key)}
          hint={fieldHint(field.key)}
          revertLabel={t('settings.revert')}
          codeLabel={t('settings.tiers.code')}
          discountLabel={t('settings.tiers.discount')}
          marginLabel={t('settings.tiers.margin')}
          marginNoneLabel={t('settings.tiers.margin_none')}
        />
      )
    }
    if (field.kind === 'table') {
      return (
        <LadderEditor
          key={field.key}
          field={field}
          locale={locale}
          draft={drafts[field.key]}
          onChange={(value) => setDraft(field.key, value)}
          onRevert={() => revert(field.key)}
          name={fieldName(field.key)}
          hint={fieldHint(field.key)}
          revertLabel={t('settings.revert')}
          stepLabel={t('settings.ladder.step')}
          fromLabel={t('settings.ladder.from')}
          discountLabel={t('settings.ladder.discount')}
          addLabel={t('settings.ladder.add')}
          removeLabel={t('settings.ladder.remove')}
          checkLabel={t('settings.ladder.check')}
        />
      )
    }
    return (
      <FieldRow
        key={field.key}
        field={field}
        locale={locale}
        draft={drafts[field.key]}
        onChange={(value) => setDraft(field.key, value)}
        onRevert={() => revert(field.key)}
        name={fieldName(field.key)}
        hint={fieldHint(field.key)}
        unitLabel={UNITS[field.key] ? t(`settings.unit.${UNITS[field.key]}` as MessageKey) : ''}
        optionLabel={(value) => optionLabel(field.key, value)}
        wasLabel={t('settings.was', { value: String(field.value) })}
        revertLabel={t('settings.revert')}
        secretSetLabel={t('settings.secret_set')}
        secretReplaceLabel={t('settings.secret_replace')}
      />
    )
  }

  // Group the section's fields into consecutive buckets by `group`, so the
  // screen draws the kit's `.hv-panel` headings instead of one flat list.
  const sectionFields = sections?.find((section) => section.id === current)?.fields ?? []
  const buckets: Array<{ group: string | null; fields: SettingView[] }> = []
  for (const field of sectionFields) {
    const last = buckets[buckets.length - 1]
    if (last && last.group === field.group) last.fields.push(field)
    else buckets.push({ group: field.group, fields: [field] })
  }

  return (
    <section className="settings">
      {error && (
        <p className="hv-hint hv-bad" role="alert">
          {error}
        </p>
      )}

      <div className="hv-cols hv-cols--2l">
        <aside className="hv-sticky hv-stack">
          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>{t('settings.sections')}</span>
              <span className="hv-panel__aside">{t('settings.sections.count', { count: sections?.length ?? 0 })}</span>
            </div>
            <TabRail
              tabs={tabs}
              current={current ?? ''}
              onSelect={setActive}
              label={t('settings.sections')}
            />
            <div className="hv-panel__foot">
              <span>{t('settings.audit_foot')}</span>
            </div>
          </section>

          <section className="hv-frame">
            <span className="hv-label">{t('settings.snapshot.title')}</span>
            <p className="hv-micro" style={{ margin: 0 }}>
              {t('settings.snapshot.note')}
            </p>
          </section>
        </aside>

        <div className="hv-stack">
          {current && (
            <TabView name={current}>
              <div className="hv-frame hv-frame--wide">
                <span className="hv-micro">
                  {`РАЗДЕЛ ${String((sections?.findIndex((section) => section.id === current) ?? -1) + 1).padStart(2, '0')} · ${current.toUpperCase()}`}
                </span>
                <h1
                  className="hv-display"
                  style={{ fontSize: 'clamp(1.5rem,3.6vw,2.4rem)', marginTop: 'var(--hv-2)' }}
                >
                  {t(`settings.section.${current}` as MessageKey)}
                </h1>
                <p className="hv-prose" style={{ marginTop: 'var(--hv-3)' }}>
                  {t(`settings.section.${current}.desc` as MessageKey)}
                </p>
              </div>

              {/* Keyed by position, not by group name. The server keeps a
                  group's fields contiguous so a heading appears once, but a key
                  that assumes it turns a server-side slip into duplicate React
                  keys — siblings sharing a key have their DOM reused across
                  each other, which is a worse failure than a repeated title. */}
              {buckets.map((bucket, index) => {
                const body = bucket.fields.map((field) => renderField(field))
                if (bucket.group === null) {
                  return <div key={index}>{body}</div>
                }
                return (
                  <section className="hv-panel" key={index}>
                    <div className="hv-panel__head">
                      <span>{text(`settings.group.${bucket.group}`)}</span>
                    </div>
                    <div className="hv-panel__body--none">{body}</div>
                  </section>
                )
              })}

              {current === 'maintenance' && (
                <>
                  <IrreversibleOps
                    locale={locale}
                    farmName={farmNameValue}
                    onDone={() => void load()}
                  />
                  <AuditLog locale={locale} rows={history} />
                </>
              )}
            </TabView>
          )}

          <SaveBar
            count={dirtyKeys.length}
            onSave={() => void save()}
            onCancel={discard}
            saving={saving}
            noChanges={t('settings.save_bar.no_changes')}
            changes={t('settings.save_bar.changes', { count: dirtyKeys.length })}
            hint={t('settings.save_bar.snapshot_hint')}
            cancelLabel={t('common.cancel')}
            saveLabel={t('common.save')}
          />
        </div>
      </div>
    </section>
  )
}

/** One `.hv-set` row, drawn per the field's kind. */
function FieldRow(props: {
  field: SettingView
  locale: Locale
  draft: unknown
  onChange: (value: unknown) => void
  onRevert: () => void
  name: string
  hint: string
  unitLabel: string
  optionLabel: (value: string) => string
  wasLabel: string
  revertLabel: string
  secretSetLabel: string
  secretReplaceLabel: string
}) {
  const { field, draft, onChange, onRevert } = props
  const dirty = draft !== undefined && !sameValue(draft, field.value)
  const boolValue = Boolean(draft ?? field.value)
  const textValue = String(draft ?? field.value ?? '')

  return (
    <div className="hv-set" data-changed={dirty}>
      <span>
        <span className="hv-set__name">{props.name}</span>
        {props.hint && <span className="hv-set__hint">{props.hint}</span>}
        <span className="hv-set__code">{field.key}</span>
      </span>
      <span className="hv-set__v">
        {dirty && field.kind !== 'secret' && <span className="hv-set__was">{props.wasLabel}</span>}
        {dirty && (
          <button className="hv-btn hv-btn--sm" type="button" onClick={onRevert}>
            {props.revertLabel}
          </button>
        )}

        {field.kind === 'boolean' && (
          <button
            className="hv-switch"
            type="button"
            role="switch"
            aria-checked={boolValue}
            aria-label={props.name}
            onClick={() => onChange(!boolValue)}
          />
        )}

        {field.kind === 'enum' && (
          <select
            className="hv-select"
            aria-label={props.name}
            value={textValue}
            onChange={(event) => onChange(event.target.value)}
          >
            {field.options.map((option) => (
              <option key={option} value={option}>
                {props.optionLabel(option)}
              </option>
            ))}
          </select>
        )}

        {field.kind === 'secret' && <SecretControl {...props} />}

        {(field.kind === 'string' || field.kind === 'integer' || field.kind === 'decimal') &&
          (isWeight(field.key) ? (
            <span className="hv-weight">
              <input
                className="hv-range"
                type="range"
                min="0"
                max="10"
                step="1"
                value={textValue}
                onChange={(event) => onChange(event.target.value)}
              />
              <span className="hv-weight__v">{textValue}</span>
            </span>
          ) : props.unitLabel ? (
            <span className="hv-unit">
              <input
                type={field.kind === 'string' ? 'text' : 'number'}
                aria-label={props.name}
                step={field.kind === 'decimal' ? 'any' : undefined}
                value={textValue}
                onChange={(event) => onChange(event.target.value)}
              />
              <span className="hv-unit__u">{props.unitLabel}</span>
            </span>
          ) : (
            <input
              className="hv-input"
              type={field.kind === 'string' ? 'text' : 'number'}
              aria-label={props.name}
              step={field.kind === 'decimal' ? 'any' : undefined}
              value={textValue}
              onChange={(event) => onChange(event.target.value)}
            />
          ))}
      </span>
    </div>
  )
}

/** The volume ladder: a table of rungs, not a number in a box. */
function LadderEditor(props: {
  field: SettingView
  locale: Locale
  draft: unknown
  onChange: (value: unknown) => void
  onRevert: () => void
  name: string
  hint: string
  revertLabel: string
  stepLabel: string
  fromLabel: string
  discountLabel: string
  addLabel: string
  removeLabel: string
  checkLabel: string
}) {
  const t = (key: MessageKey) => translate(props.locale, key)
  const rungs = (
    props.draft !== undefined ? (props.draft as LadderRung[]) : (props.field.value as LadderRung[])
  ) ?? []
  const dirty = props.draft !== undefined && !sameValue(props.draft, props.field.value)

  const update = (index: number, patch: Partial<LadderRung>) =>
    props.onChange(rungs.map((rung, i) => (i === index ? { ...rung, ...patch } : rung)))

  const remove = (index: number) => props.onChange(rungs.filter((_, i) => i !== index))

  const add = () => {
    const last = rungs[rungs.length - 1]?.min_quantity ?? 0
    props.onChange([...rungs, { min_quantity: last + 10, percent: '0' }])
  }

  return (
    <section className="hv-panel" data-changed={dirty}>
      <div className="hv-panel__head">
        <span>{props.name}</span>
        <span className="hv-panel__aside">
          {dirty && (
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
              <th>{props.stepLabel}</th>
              <th>{props.fromLabel}</th>
              <th>{props.discountLabel}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rungs.map((rung, index) => (
              <tr key={index}>
                <td className="hv-table__id">{index + 1}</td>
                <td>
                  <span className="hv-unit">
                    <input
                      type="number"
                      min="1"
                      aria-label={`${props.fromLabel} ${index + 1}`}
                      value={rung.min_quantity}
                      onChange={(event) =>
                        update(index, { min_quantity: Number(event.target.value) })
                      }
                    />
                    <span className="hv-unit__u">{t('settings.unit.pieces')}</span>
                  </span>
                </td>
                <td>
                  <span className="hv-unit">
                    <input
                      type="number"
                      min="0"
                      step="1"
                      aria-label={`${props.discountLabel} ${index + 1}`}
                      value={rung.percent}
                      onChange={(event) => update(index, { percent: event.target.value })}
                    />
                    <span className="hv-unit__u">{t('settings.unit.percent')}</span>
                  </span>
                </td>
                <td>
                  <button
                    className="hv-btn hv-btn--sm hv-btn--danger"
                    type="button"
                    onClick={() => remove(index)}
                  >
                    {props.removeLabel}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="hv-panel__foot">
        <span>{props.checkLabel}</span>
        <button className="hv-btn hv-btn--sm" type="button" onClick={add}>
          {props.addLabel}
        </button>
      </div>
    </section>
  )
}

/** The customer tiers: code fixed, discount and margin override editable. */
function TiersEditor(props: {
  field: SettingView
  locale: Locale
  draft: unknown
  onChange: (value: unknown) => void
  onRevert: () => void
  name: string
  hint: string
  revertLabel: string
  codeLabel: string
  discountLabel: string
  marginLabel: string
  marginNoneLabel: string
}) {
  const t = (key: MessageKey) => translate(props.locale, key)
  const rows = (
    props.draft !== undefined ? (props.draft as TierRow[]) : (props.field.value as TierRow[])
  ) ?? []
  const dirty = props.draft !== undefined && !sameValue(props.draft, props.field.value)

  const update = (index: number, patch: Partial<TierRow>) =>
    props.onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  return (
    <section className="hv-panel" data-changed={dirty}>
      <div className="hv-panel__head">
        <span>{props.name}</span>
        <span className="hv-panel__aside">
          {dirty && (
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
              <th>{props.codeLabel}</th>
              <th>{props.discountLabel}</th>
              <th>{props.marginLabel}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={row.code}>
                <td className="hv-table__id">{row.code}</td>
                <td>
                  <span className="hv-unit">
                    <input
                      type="number"
                      min="0"
                      step="1"
                      aria-label={`${props.discountLabel} ${row.code}`}
                      value={row.discount_percent}
                      onChange={(event) =>
                        update(index, { discount_percent: event.target.value })
                      }
                    />
                    <span className="hv-unit__u">{t('settings.unit.percent')}</span>
                  </span>
                </td>
                <td>
                  <span className="hv-unit">
                    <input
                      type="number"
                      step="1"
                      aria-label={`${props.marginLabel} ${row.code}`}
                      placeholder={props.marginNoneLabel}
                      value={row.margin_percent_override ?? ''}
                      onChange={(event) =>
                        update(index, {
                          margin_percent_override:
                            event.target.value === '' ? null : event.target.value,
                        })
                      }
                    />
                    <span className="hv-unit__u">{t('settings.unit.percent')}</span>
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

/** A secret: a saved badge + «Replace», or an empty password field. Never the value. */
function SecretControl(props: {
  field: SettingView
  draft: unknown
  onChange: (value: unknown) => void
  name: string
  secretSetLabel: string
  secretReplaceLabel: string
}) {
  const { field, draft, onChange, name, secretSetLabel, secretReplaceLabel } = props
  const editing = draft !== undefined

  if (field.is_set && !editing) {
    return (
      <>
        <span className="hv-micro hv-good">{secretSetLabel}</span>
        <button className="hv-btn hv-btn--sm" type="button" onClick={() => onChange('')}>
          {secretReplaceLabel}
        </button>
      </>
    )
  }

  return (
    <input
      className="hv-input"
      type="password"
      aria-label={name}
      autoComplete="new-password"
      placeholder={field.is_set ? '••••••••' : ''}
      value={String(draft ?? '')}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

/** The pinned save bar: quiet until something is dirty. */
function SaveBar(props: {
  count: number
  onSave: () => void
  onCancel: () => void
  saving: boolean
  noChanges: string
  changes: string
  hint: string
  cancelLabel: string
  saveLabel: string
}) {
  const dirty = props.count > 0
  return (
    <div className="hv-savebar" data-dirty={dirty}>
      <span className="hv-savebar__n">
        {dirty ? props.changes : props.noChanges}
      </span>
      <span className="hv-spacer" />
      <span className="hv-micro">{props.hint}</span>
      <button
        className="hv-btn"
        type="button"
        disabled={!dirty || props.saving}
        onClick={props.onCancel}
      >
        {props.cancelLabel}
      </button>
      <button
        className="hv-btn hv-btn--primary"
        type="button"
        disabled={!dirty || props.saving}
        onClick={props.onSave}
      >
        {props.saveLabel}
      </button>
    </div>
  )
}

/** «Было · Стало» for the whole farm, under «Обслуживание системы». */
function AuditLog({ locale, rows }: { locale: Locale; rows: SettingChange[] | null }) {
  const t = (key: MessageKey) => translate(locale, key)

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('settings.audit.title')}</span>
        <span className="hv-panel__aside">{t('settings.audit_foot')}</span>
      </div>
      <div className="hv-panel__body--none">
        {!rows || rows.length === 0 ? (
          <p className="hv-micro" style={{ padding: 'var(--hv-3)' }}>
            {t('settings.audit.empty')}
          </p>
        ) : (
          <table className="hv-table">
            <thead>
              <tr>
                <th>{t('settings.audit.time')}</th>
                <th>{t('settings.audit.who')}</th>
                <th>{t('settings.audit.key')}</th>
                <th>{t('settings.audit.was')}</th>
                <th>{t('settings.audit.became')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.key}-${row.changed_at}`}>
                  <td className="hv-table__id">
                    {new Date(row.changed_at).toLocaleString(locale)}
                  </td>
                  <td>{row.changed_by_name ?? '—'}</td>
                  <td className="hv-table__id">{row.key}</td>
                  <td>{formatCell(row.old_value)}</td>
                  <td>{formatCell(row.new_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}

/** A stored value as the audit writes it: a dash for «was nothing», raw otherwise. */
function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  return String(value)
}

/** The kit's «Необратимые операции» — one of them, the safe one, is wired. */
function IrreversibleOps(props: { locale: Locale; farmName: string; onDone: () => void }) {
  const t = (key: MessageKey) => translate(props.locale, key)

  return (
    <section className="hv-frame hv-danger">
      <span className="hv-h hv-bad">{t('settings.irreversible.title')}</span>
      <p className="hv-prose" style={{ fontSize: 'var(--hv-size-small)', marginTop: 'var(--hv-2)' }}>
        {t('settings.irreversible.note')}
      </p>

      <div className="hv-stack hv-stack--2" style={{ marginTop: 'var(--hv-3)' }}>
        <ConfirmAction
          locale={props.locale}
          farmName={props.farmName}
          name={t('settings.irreversible.reset_rates')}
          hint={t('settings.irreversible.reset_rates.hint')}
          actionLabel={t('settings.irreversible.reset')}
          onRun={async () => {
            await api.post('/settings/reset-rates', undefined)
            props.onDone()
          }}
        />
        <hr className="hv-hr" />
        <ConfirmAction
          locale={props.locale}
          farmName={props.farmName}
          name={t('settings.irreversible.drop_telemetry')}
          hint={t('settings.irreversible.drop_telemetry.hint')}
          actionLabel={t('settings.irreversible.drop')}
          onRun={async () => {
            await api.post('/settings/drop-telemetry', undefined)
            props.onDone()
          }}
        />
      </div>
    </section>
  )
}

/** One destructive action, armed only after the farm name is typed. */
function ConfirmAction(props: {
  locale: Locale
  farmName: string
  name: string
  hint: string
  actionLabel: string
  onRun: () => Promise<void>
}) {
  const t = (key: MessageKey) => translate(props.locale, key)
  const [confirming, setConfirming] = useState(false)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      await props.onRun()
      setConfirming(false)
      setTyped('')
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(props.locale, { code: exc.code, details: exc.details })
          : translate(props.locale, 'error.internal'),
      )
    } finally {
      setBusy(false)
    }
  }

  // A farm with no name cannot confirm anything. Comparing two empty strings
  // armed «Подтвердить» before the owner typed a character, which turned a
  // deliberate two-step into one click on an operation that does not come back.
  const expected = props.farmName.trim()
  const confirmed = expected.length > 0 && typed.trim() === expected

  return (
    <>
      <div className="hv-row hv-row--between">
        <span>
          <span className="hv-set__name">{props.name}</span>
          <span className="hv-set__hint">{props.hint}</span>
        </span>
        {confirming ? (
          expected.length === 0 ? (
            // Not a disabled button with no explanation: typing can never match,
            // so say why rather than leave the owner guessing at a dead control.
            <span className="hv-hint hv-bad">{t('settings.irreversible.needs_name')}</span>
          ) : (
            <span className="hv-row">
              <input
                className="hv-input"
                aria-label={t('settings.irreversible.prompt')}
                placeholder={t('settings.irreversible.prompt')}
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
              />
              <button
                className="hv-btn hv-btn--sm hv-btn--danger"
                type="button"
                disabled={!confirmed || busy}
                onClick={() => void run()}
              >
                {t('settings.irreversible.confirm')}
              </button>
            </span>
          )
        ) : (
          <button
            className="hv-btn hv-btn--danger"
            type="button"
            onClick={() => setConfirming(true)}
          >
            {props.actionLabel}
          </button>
        )}
      </div>
      {error && (
        <p className="hv-hint hv-bad" role="alert">
          {error}
        </p>
      )}
    </>
  )
}
