import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Column, Locale, MessageKey, StatusTag } from '@printorian/ui'
import {
  DataTable,
  Modal,
  api,
  formatMoney,
  summarizeLocations,
  translate,
  translateError,
  useChrome,
  useSession,
} from '@printorian/ui'

import { Field } from './FleetAdmin'
import { MaterialDetail } from './MaterialDetail'
import type { MaterialLot } from './MaterialDetail'

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

/**
 * One row of `GET /materials`, which is a *subset* of what a material is.
 *
 * The physical properties and the purchase price ride in the same response and
 * are deliberately not declared here: the window that shows them reads
 * `GET /materials/{code}` and types it there, so this screen cannot start
 * rendering a detail out of a row that a future paged listing would trim.
 */
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
  lots: MaterialLot[]
}

interface MaterialTable {
  rows: Material[]
  counts: { status: string; count: number }[]
}

export function MaterialsPage({ locale }: { locale: Locale }) {
  const { actor } = useSession()
  const t = useCallback((key: MessageKey) => translate(locale, key), [locale])

  const [table, setTable] = useState<MaterialTable | null>(null)

  /* The kit's `INVENTORY.SPECS[24] · LOTS[61]`, split into two labelled pairs. */
  useChrome(
    table
      ? {
          meta: [
            { label: 'INVENTORY.SPECS', value: String(table.rows.length) },
            {
              label: 'LOTS',
              value: String(table.rows.reduce((sum, row) => sum + (row.lots?.length ?? 0), 0)),
            },
          ],
        }
      : null,
  )
  const [error, setError] = useState<string | null>(null)
  /*
    The *code* of the open material, not the row it was opened from.

    The window re-reads the material by code (`MaterialDetail`), so holding a row
    here would only give it a second, staler copy of the same facts to disagree
    with. It also means the table can reload underneath without the popup
    flickering or reverting.
  */
  const [selected, setSelected] = useState<string | null>(null)
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
    void (async () => {
      await load()
    })()
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
        {/* Stays mounted *and* focusable while the popup is open. It used to
            unmount, which left the modal's focus-restore with nothing to return
            to; disabling it instead was the same bug in a different costume,
            because a disabled control cannot take focus either. `aria-expanded`
            carries the state, and the backdrop is what stops it being clicked. */}
        {mayManage && (
          <button
            className="hv-btn"
            type="button"
            onClick={() => setAdding(true)}
            aria-expanded={adding}
          >
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

      {error && (
          <p className="hv-hint hv-bad" role="alert">
            {error}
          </p>
        )}

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
        onRowActivate={(row) => setSelected(row.code)}
      />

      {/* The material's own window, as the scenario's table asks (item M1) and
          the kit draws it. It was a panel below the table, so opening a row
          pushed that row out from under the pointer — and the "add lot" form
          inside it now sits in a popup for free.

          Keyed by the code so that opening a different material starts a fresh
          fetch rather than showing the previous one's figures while the new
          request is in flight. */}
      {selected && (
        <MaterialDetail
          key={selected}
          code={selected}
          locale={locale}
          printerNames={printerNames}
          onClose={() => setSelected(null)}
          onChanged={() => void load()}
        />
      )}
    </section>
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
  /*
    The same popup the fleet uses, for the same reason: a material is a thing with
    an identity, and creating one is not an edit to the table it will appear in.
  */
  return (
    <Modal
      title={`${t('materials.add.title')} :: ${t('materials.title')}`}
      path="/INVENTORY/MATERIALS/NEW"
      onClose={onCancel}
      footer={
        <>
          <span>ЦЕНА ДЛЯ ЗАКАЗЧИКА ЗАДАЁТСЯ ЗА ГРАММ</span>
          <span className="hv-row">
            <button className="hv-btn" type="button" onClick={onCancel} disabled={busy}>
              {t('common.cancel')}
            </button>
            <button
              className="hv-btn hv-btn--primary"
              type="submit"
              form="material-form"
              disabled={busy}
            >
              {t('common.save')}
            </button>
          </span>
        </>
      }
    >
      <form
        className="admin-form"
        id="material-form"
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

      {error && (
          <p className="hv-hint hv-bad" role="alert">
            {error}
          </p>
        )}

      </form>
    </Modal>
  )
}
