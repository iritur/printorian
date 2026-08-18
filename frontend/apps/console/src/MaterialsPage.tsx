import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Column, Locale, MessageKey, StatusTag } from '@printorian/ui'
import {
  DataTable,
  api,
  formatLocation,
  formatMoney,
  summarizeLocations,
  translate,
  translateError,
  useSession,
} from '@printorian/ui'

import { Field } from './FleetAdmin'

/**
 * The materials table (scenario item M1), with the inventory actions behind
 * `manage_inventory`.
 *
 * `status` and `total_remaining_grams` are derived server-side from the lots, so
 * adding a lot changes the row's status without this screen knowing the rule.
 */

const MANAGE_INVENTORY = 'manage_inventory'
const VIEW_PRODUCTION = 'view_production'
const STATUSES = ['stock', 'in_printer', 'ordered', 'none'] as const

interface Lot {
  id: string
  label: string
  remaining_grams: string
  location_kind: string
  shelf: string | null
  printer_id: string | null
  ams_unit: number | null
  ams_slot: number | null
}

interface Material {
  id: string
  code: string
  name: string
  family: string
  color_name: string
  color_hex: string
  sell_price_per_gram: string
  status: string
  total_remaining_grams: string
  lot_count: number
  lots: Lot[]
}

interface MaterialTable {
  rows: Material[]
  counts: { status: string; count: number }[]
}

export function MaterialsPage({ locale }: { locale: Locale }) {
  const { actor } = useSession()
  const t = useCallback((key: MessageKey) => translate(locale, key), [locale])

  const [table, setTable] = useState<MaterialTable | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Material | null>(null)
  const [adding, setAdding] = useState(false)
  const [printerNames, setPrinterNames] = useState<Record<string, string>>({})

  const mayManage = actor?.permissions.includes(MANAGE_INVENTORY) ?? false
  const seesFleet = actor?.permissions.includes(VIEW_PRODUCTION) ?? false

  const load = useCallback(async () => {
    try {
      setTable(await api.get<MaterialTable>('/materials'))
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
    void load()
  }, [load])

  // Lots carry a printer id, not a name. Resolving it is a separate, optional
  // request: someone who may read materials but not the fleet still gets a
  // useful table, just without machine names (see `formatLocation`).
  useEffect(() => {
    if (!seesFleet) return
    api
      .get<{ rows: { id: string; name: string }[] }>('/printers')
      .then((fleet) =>
        setPrinterNames(Object.fromEntries(fleet.rows.map((row) => [row.id, row.name]))),
      )
      .catch(() => setPrinterNames({}))
  }, [seesFleet])

  const columns = useMemo<Column<Material>[]>(
    () => [
      { key: 'code', header: t('materials.code'), value: (row) => row.code },
      {
        key: 'name',
        header: t('materials.name'),
        value: (row) => row.name,
        render: (row) => (
          <span className="materials__name">
            <span
              className="materials__swatch"
              style={{ background: row.color_hex }}
              aria-hidden="true"
            />
            {row.name}
          </span>
        ),
      },
      { key: 'family', header: t('materials.family'), value: (row) => row.family },
      {
        key: 'status',
        header: t('order.status'),
        value: (row) => row.status,
        render: (row) => translate(locale, `material.status.${row.status}` as MessageKey),
      },
      {
        key: 'stock',
        header: t('materials.stock'),
        align: 'end',
        value: (row) => Number(row.total_remaining_grams),
        render: (row) => Number(row.total_remaining_grams).toFixed(0),
      },
      {
        key: 'location',
        header: t('materials.location'),
        // Sorted and displayed by the same summary string, so the column orders
        // the way it reads.
        value: (row) => summarizeLocations(row.lots, locale, printerNames),
        render: (row) => summarizeLocations(row.lots, locale, printerNames) || t('common.none'),
      },
      {
        key: 'price',
        header: t('materials.price'),
        align: 'end',
        // Stored per gram; a person buys filament by the kilogram.
        value: (row) => Number(row.sell_price_per_gram),
        render: (row) =>
          formatMoney((Number(row.sell_price_per_gram) * 1000).toFixed(2), 'RUB', locale),
      },
    ],
    [locale, printerNames, t],
  )

  const tags = useMemo<StatusTag<Material>[]>(
    () =>
      STATUSES.map((status) => ({
        key: status,
        label: translate(locale, `material.status.${status}` as MessageKey),
        match: (row: Material) => row.status === status,
        tone:
          status === 'none'
            ? ('bad' as const)
            : status === 'stock'
              ? ('good' as const)
              : ('neutral' as const),
      })),
    [locale],
  )

  return (
    <section className="materials">
      <header className="fleet__header">
        <h2>{t('materials.title')}</h2>
        {mayManage && !adding && (
          <button type="button" onClick={() => setAdding(true)}>
            {t('materials.add')}
          </button>
        )}
      </header>

      {adding && (
        <MaterialForm
          locale={locale}
          onCancel={() => setAdding(false)}
          onDone={() => {
            setAdding(false)
            void load()
          }}
        />
      )}

      {error && <p className="cfg__error">{error}</p>}

      <DataTable
        rows={table?.rows ?? []}
        columns={columns}
        rowKey={(row) => row.id}
        statusTags={tags}
        caption={t('materials.title')}
        emptyLabel={t('common.empty')}
        isLoading={table === null}
        loadingLabel={t('common.loading')}
        initialSort={{ key: 'code', direction: 'asc' }}
        onRowActivate={setSelected}
      />

      {selected && (
        <section className="admin-detail">
          <header className="admin-detail__head">
            <h3>
              {selected.code} · {selected.name}
            </h3>
            <button type="button" onClick={() => setSelected(null)}>
              {t('common.close')}
            </button>
          </header>

          <dl className="admin-detail__facts">
            <dt>{t('materials.family')}</dt>
            <dd>{selected.family}</dd>
            <dt>{t('materials.stock')}</dt>
            <dd>{Number(selected.total_remaining_grams).toFixed(0)}</dd>
            <dt>{t('materials.price')}</dt>
            <dd>
              {formatMoney(
                (Number(selected.sell_price_per_gram) * 1000).toFixed(2),
                'RUB',
                locale,
              )}
            </dd>
          </dl>

          {/* Per-lot, because the column above collapses duplicates: this is
              where someone finds which spool to physically fetch. */}
          <h4>{t('materials.lots')}</h4>
          {selected.lots.length === 0 ? (
            <p className="admin-detail__muted">{t('common.empty')}</p>
          ) : (
            <ul className="admin-detail__list">
              {selected.lots.map((lot) => (
                <li key={lot.id}>
                  <span>{lot.label || lot.id.slice(0, 8)}</span>
                  <span className="admin-detail__muted">
                    {formatLocation(lot, locale, printerNames)}
                  </span>
                  <span>{Number(lot.remaining_grams).toFixed(0)} г</span>
                  {/* Only for a spool actually in a machine — there is nothing
                      to remove from a shelf. */}
                  {mayManage && lot.location_kind === 'printer' && lot.printer_id && (
                    <UnmountButton
                      lot={lot}
                      locale={locale}
                      onDone={() => {
                        setSelected(null)
                        void load()
                      }}
                    />
                  )}
                </li>
              ))}
            </ul>
          )}

          {mayManage && (
            <LotForm
              material={selected}
              locale={locale}
              onDone={() => {
                setSelected(null)
                void load()
              }}
            />
          )}
        </section>
      )}
    </section>
  )
}

function UnmountButton({
  lot,
  locale,
  onDone,
}: {
  lot: Lot
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
      <button type="button" onClick={() => setOpen(true)}>
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
      <button type="button" disabled={busy} onClick={submit}>
        {t('common.save')}
      </button>
      <button type="button" disabled={busy} onClick={() => setOpen(false)}>
        {t('common.cancel')}
      </button>
      {error && <span className="cfg__error">{error}</span>}
    </span>
  )
}

function MaterialForm({
  locale,
  onDone,
  onCancel,
}: {
  locale: Locale
  onDone: () => void
  onCancel: () => void
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({
    code: '',
    name: '',
    family: 'PLA',
    color_name: '',
    color_hex: '#888888',
    density_g_per_cm3: '1.24',
    sell_price_per_gram: '2.40',
  })

  const set = (key: keyof typeof form) => (value: string) =>
    setForm((current) => ({ ...current, [key]: value }))

  return (
    <form
      className="admin-form"
      onSubmit={(event) => {
        event.preventDefault()
        setBusy(true)
        setError(null)
        api
          .post('/materials', form)
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
      <h3>{t('materials.add.title')}</h3>

      <div className="admin-form__grid">
        <Field label={t('materials.code')}>
          <input required value={form.code} onChange={(e) => set('code')(e.target.value)} />
        </Field>
        <Field label={t('materials.name')}>
          <input required value={form.name} onChange={(e) => set('name')(e.target.value)} />
        </Field>
        <Field label={t('materials.family')}>
          <input required value={form.family} onChange={(e) => set('family')(e.target.value)} />
        </Field>
        <Field label={t('materials.color')}>
          <input
            value={form.color_name}
            onChange={(e) => set('color_name')(e.target.value)}
          />
        </Field>
        <Field label={t('materials.color_hex')}>
          <input
            type="color"
            value={form.color_hex}
            onChange={(e) => set('color_hex')(e.target.value)}
          />
        </Field>
        <Field label={t('materials.density')}>
          <input
            type="number"
            min="0.1"
            step="0.01"
            required
            value={form.density_g_per_cm3}
            onChange={(e) => set('density_g_per_cm3')(e.target.value)}
          />
        </Field>
        {/* Priced per gram, matching the engine's rate unit. The table shows the
            per-kilogram figure because that is how filament is bought. */}
        <Field label={t('materials.price_gram')}>
          <input
            type="number"
            min="0"
            step="0.01"
            required
            value={form.sell_price_per_gram}
            onChange={(e) => set('sell_price_per_gram')(e.target.value)}
          />
        </Field>
      </div>

      {error && <p className="cfg__error">{error}</p>}

      <div className="admin-form__actions">
        <button type="submit" disabled={busy}>
          {t('common.save')}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>
          {t('common.cancel')}
        </button>
      </div>
    </form>
  )
}

function LotForm({
  material,
  locale,
  onDone,
}: {
  material: Material
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
      <button type="submit" disabled={busy}>
        {t('materials.add_lot')}
      </button>
      {error && <p className="cfg__error">{error}</p>}
    </form>
  )
}
