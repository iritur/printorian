import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale } from '@printorian/ui'
import { Modal, api, translateError, useSession } from '@printorian/ui'

import { Field } from './FleetAdmin'

/**
 * The model library, from the farm's side — `manage_library`.
 *
 * The storefront's catalogue screen is the same data read by anyone; this is the
 * half only staff reach. It lives in the console rather than behind a flag on the
 * storefront because ADR-0016 puts staff work in a separate bundle: a customer
 * cannot reach an editor's screen by editing a permission in devtools, because the
 * code is on another machine.
 *
 * **Geometry comes first.** Uploading the mesh is step one and it answers with
 * what the farm measured — triangles, bounding box, volume, whether it is
 * watertight. The form then describes a part whose facts are already known, rather
 * than collecting a description and discovering at publish time that the mesh
 * cannot be priced.
 */

const MANAGE_LIBRARY = 'manage_library'

const CATEGORIES = [
  ['func', 'Функциональные'],
  ['case', 'Корпуса и боксы'],
  ['mech', 'Механика'],
  ['org', 'Организация'],
  ['decor', 'Декор'],
] as const

/** The six editorial 0–10 bars, in the order the storefront draws them. */
const BARS = [
  ['difficulty', 'Сложность'],
  ['strength', 'Прочность'],
  ['accuracy', 'Точность'],
  ['speed', 'Скорость'],
  ['supports', 'Поддержки'],
  ['postprocessing', 'Постобработка'],
] as const

type BarKey = (typeof BARS)[number][0]

interface UploadedGeometry {
  model_asset_id: string
  filename: string
  triangle_count: number
  volume_cm3: string
  width_mm: string
  depth_mm: string
  height_mm: string
  is_watertight: boolean
  is_priceable: boolean
  size_class: string
}

type Suitability = 'excellent' | 'good' | 'limited'

/** One material an editor offers a model in, with the kit's three judgements. */
interface MaterialOffer {
  code: string
  suitability: Suitability
  note: string
  is_recommended: boolean
}

interface SuitableMaterial extends MaterialOffer {
  name: string
  price_delta: string | null
  stock_grams: string | null
}

const SUITABILITY: [Suitability, string][] = [
  ['excellent', 'Отлично'],
  ['good', 'Хорошо'],
  ['limited', 'Ограниченно'],
]

interface CatalogRow {
  id: string
  slug: string
  code: string
  title: string
  category: string
  size_class: string
  is_watertight: boolean
  print_count: number
  materials: string[]
  published_at: string | null
  volume_cm3: string
}

interface Draft {
  slug: string
  code: string
  title: string
  summary: string
  category: string
  author: string
  license: string
  version: string
  materials: MaterialOffer[]
  multicolor: boolean
  is_published: boolean
  bars: Record<BarKey, number>
}

const EMPTY: Draft = {
  slug: '',
  code: '',
  title: '',
  summary: '',
  category: 'func',
  author: '',
  license: '',
  version: '',
  materials: [{ code: 'pla', suitability: 'excellent', note: '', is_recommended: true }],
  multicolor: false,
  is_published: false,
  bars: { difficulty: 0, strength: 0, accuracy: 0, speed: 0, supports: 0, postprocessing: 0 },
}

/** `Кронштейн угловой V4` → `kronshteyn-uglovoy-v4`, near enough to start from. */
const TRANSLIT: Record<string, string> = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i',
  й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't',
  у: 'u', ф: 'f', х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'sch', ъ: '', ы: 'y', ь: '',
  э: 'e', ю: 'yu', я: 'ya',
}

function slugify(title: string): string {
  return [...title.toLowerCase()]
    .map((char) => TRANSLIT[char] ?? char)
    .join('')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120)
}

export function LibraryPage({ locale }: { locale: Locale }) {
  const { actor } = useSession()
  const may = actor?.permissions.includes(MANAGE_LIBRARY) ?? false

  const [rows, setRows] = useState<CatalogRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [geometry, setGeometry] = useState<UploadedGeometry | null>(null)
  const [draft, setDraft] = useState<Draft>(EMPTY)
  const [editing, setEditing] = useState<string | null>(null)
  /*
    Whether the editor popup is open for a *new* model.

    `editing` already carries "which model is loaded", but the panel used to be
    permanently on screen so creating had no state of its own. A popup has to
    know whether it is open, and the two cases stay separate because they are:
    one loads a record, the other starts an empty one.
  */
  const [composing, setComposing] = useState(false)
  const [busy, setBusy] = useState(false)

  // Stable, so `load` can depend on it honestly instead of the lint rule being
  // silenced. The backend sends codes, never prose (ADR-0012), so every error
  // the user reads is translated here.
  const report = useCallback(
    (exc: unknown) =>
      setError(exc instanceof ApiError ? translateError(locale, exc) : String(exc)),
    [locale],
  )

  const load = useCallback(async () => {
    try {
      // Staff see drafts as well as published entries — the same endpoint, one
      // predicate wider, rather than a second view that would drift from it.
      const table = await api.get<{ rows: CatalogRow[] }>('/catalog?limit=96')
      setRows(table.rows)
    } catch (exc) {
      report(exc)
    }
  }, [report])

  useEffect(() => {
    if (may) void load()
  }, [may, load])

  if (!may) {
    return <p className="hv-hint">Нужно право «manage_library».</p>
  }

  const upload = async (file: File) => {
    setBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const uploaded = await api.upload<UploadedGeometry>('/catalog/geometry', form)
      setGeometry(uploaded)
      // Only fill what the editor has not typed — re-uploading a mesh must not
      // overwrite a title somebody has already written.
      setDraft((current) => ({
        ...current,
        title: current.title || uploaded.filename.replace(/\.[^.]+$/, ''),
        code: current.code || uploaded.filename.replace(/\.[^.]+$/, '').toUpperCase(),
        slug: current.slug || slugify(uploaded.filename.replace(/\.[^.]+$/, '')),
      }))
    } catch (exc) {
      report(exc)
    } finally {
      setBusy(false)
    }
  }

  const patchMaterial = (index: number, patch: Partial<MaterialOffer>) =>
    setDraft((current) => ({
      ...current,
      materials: current.materials.map((offer, i) =>
        i === index ? { ...offer, ...patch } : offer,
      ),
    }))

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const body = {
        ...draft,
        ...draft.bars,
        materials: draft.materials
          .filter((offer) => offer.code.trim())
          .map((offer) => ({ ...offer, code: offer.code.trim().toLowerCase() })),
        model_asset_id: geometry?.model_asset_id,
      }
      delete (body as Record<string, unknown>).bars
      if (editing) {
        // No `model_asset_id` unless a new mesh was uploaded: sending the same one
        // would make the server re-read megabytes and redraw an identical preview.
        if (!geometry) delete (body as Record<string, unknown>).model_asset_id
        await api.patch(`/catalog/${editing}`, body)
      } else {
        await api.post('/catalog', body)
      }
      setDraft(EMPTY)
      setGeometry(null)
      setEditing(null)
      await load()
    } catch (exc) {
      report(exc)
    } finally {
      setBusy(false)
    }
  }

  const edit = (row: CatalogRow) => {
    setComposing(false)
    setEditing(row.slug)
    setGeometry(null)
    setError(null)
    void (async () => {
      try {
        const full = await api.get<
          CatalogRow & Draft & Record<BarKey, number> & { suitable_materials: SuitableMaterial[] }
        >(
          `/catalog/${row.slug}`,
        )
        setDraft({
          slug: full.slug,
          code: full.code,
          title: full.title,
          summary: full.summary ?? '',
          category: full.category,
          author: full.author ?? '',
          license: full.license ?? '',
          version: full.version ?? '',
          // The detail endpoint answers with the composed table — grade, note
          // and recommendation included — so an edit round-trips without losing
          // the judgements somebody already made.
          materials: (full.suitable_materials ?? []).map((entry) => ({
            code: entry.code,
            suitability: entry.suitability,
            note: entry.note ?? '',
            is_recommended: Boolean(entry.is_recommended),
          })),
          multicolor: Boolean(full.multicolor),
          is_published: Boolean(full.published_at),
          bars: Object.fromEntries(
            BARS.map(([key]) => [key, Number(full[key] ?? 0)]),
          ) as Record<BarKey, number>,
        })
      } catch (exc) {
        report(exc)
      }
    })()
  }

  const remove = async (slug: string) => {
    setBusy(true)
    try {
      await api.delete(`/catalog/${slug}`)
      if (editing === slug) {
        setEditing(null)
        setDraft(EMPTY)
      }
      await load()
    } catch (exc) {
      report(exc)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="hv-stack">
      {/* ------------------------------------------------------------ list */}
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>Библиотека моделей</span>
          <span className="hv-panel__aside">{rows?.length ?? 0} ЗАПИСЕЙ</span>
          <button
            className="hv-btn hv-btn--sm"
            type="button"
            onClick={() => {
              setComposing(true)
              setEditing(null)
              setDraft(EMPTY)
              setGeometry(null)
              setError(null)
            }}
          >
            Новая модель
          </button>
        </div>
        <div className="hv-table-wrap">
          <table className="hv-table">
            <thead>
              <tr>
                <th>Код</th>
                <th>Название</th>
                <th>Раздел</th>
                <th>Размер</th>
                <th>Статус</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(rows ?? []).map((row) => (
                <tr key={row.id} data-active={editing === row.slug}>
                  <td className="hv-table__id">{row.code || '—'}</td>
                  <td>{row.title}</td>
                  <td>{CATEGORIES.find(([key]) => key === row.category)?.[1] ?? row.category}</td>
                  <td>{row.size_class.toUpperCase()}</td>
                  <td>
                    {/*
                      Two facts, not one. A draft is invisible to customers; a
                      mesh that is not watertight cannot be priced at all, so it
                      would be an entry nobody can order even once published.
                    */}
                    <span className="hv-tag" data-tone={row.published_at ? 'good' : 'warn'}>
                      <span className="hv-tag__n">
                        {row.published_at ? 'опубликована' : 'черновик'}
                      </span>
                    </span>
                    {!row.is_watertight && (
                      <span className="hv-tag" data-tone="bad">
                        <span className="hv-tag__n">не замкнута</span>
                      </span>
                    )}
                  </td>
                  <td data-align="end">
                    <button type="button" className="hv-btn hv-btn--sm" onClick={() => edit(row)}>
                      Править
                    </button>{' '}
                    <button
                      type="button"
                      className="hv-btn hv-btn--sm"
                      disabled={busy}
                      onClick={() => void remove(row.slug)}
                    >
                      Удалить
                    </button>
                  </td>
                </tr>
              ))}
              {rows?.length === 0 && (
                <tr>
                  <td colSpan={6} className="hv-hint">
                    Пока пусто. Загрузите модель справа.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* ---------------------------------------------------------- editor
          A window rather than a second column. The editor carries a file upload,
          a materials table and a dozen fields; beside the list it squeezed both,
          and below it the list scrolled away from whatever was being edited. */}
      {(composing || editing !== null) && (
        <Modal
          wide
          title={editing ? `Правка :: ${editing}` : 'Новая модель :: Каталог'}
          path={editing ? `/CATALOG/CURATION/${editing.toUpperCase()}` : '/CATALOG/CURATION/NEW'}
          pathStatus={draft.is_published ? 'СТАТУС :: В ВИТРИНЕ' : 'СТАТУС :: ЧЕРНОВИК'}
          onClose={() => {
            setComposing(false)
            setEditing(null)
            setDraft(EMPTY)
            setGeometry(null)
          }}
          footer={
            <>
              <span>
                {!editing && !geometry ? 'СНАЧАЛА ЗАГРУЗИТЕ ГЕОМЕТРИЮ' : 'ГЕОМЕТРИЯ ИЗМЕРЯЕТСЯ ПРИ ЗАГРУЗКЕ'}
              </span>
              <span className="hv-row">
                <button
                  className="hv-btn"
                  type="button"
                  onClick={() => {
                    setComposing(false)
                    setEditing(null)
                    setDraft(EMPTY)
                    setGeometry(null)
                  }}
                >
                  Отменить
                </button>
                <button
                  className="hv-btn hv-btn--primary"
                  type="button"
                  disabled={busy || !draft.title || !draft.slug || (!editing && !geometry)}
                  onClick={() => void save()}
                >
                  {editing ? 'Сохранить' : 'Добавить в каталог'}
                </button>
              </span>
            </>
          }
        >
          {error && <p className="hv-hint hv-bad">{error}</p>}

          <Field
            label="Геометрия"
            hint={
              editing
                ? 'Загрузите файл, только если геометрия изменилась — иначе она остаётся прежней.'
                : 'STL. Измеряется при загрузке: габарит, объём, герметичность.'
            }
          >
            <input
              type="file"
              accept=".stl,model/stl,application/octet-stream"
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) void upload(file)
              }}
            />
          </Field>

          {geometry && (
            <ul className="hv-leaders">
              <li className="hv-leader">
                <span className="hv-leader__k">Габарит</span>
                <i className="hv-leader__fill" />
                <span className="hv-leader__v">
                  {Number(geometry.width_mm).toFixed(1)} × {Number(geometry.depth_mm).toFixed(1)} ×{' '}
                  {Number(geometry.height_mm).toFixed(1)} мм · {geometry.size_class.toUpperCase()}
                </span>
              </li>
              <li className="hv-leader">
                <span className="hv-leader__k">Объём · треугольников</span>
                <i className="hv-leader__fill" />
                <span className="hv-leader__v">
                  {Number(geometry.volume_cm3).toFixed(2)} см³ ·{' '}
                  {geometry.triangle_count.toLocaleString(locale)}
                </span>
              </li>
              <li className="hv-leader">
                <span className="hv-leader__k">Пригодна к расчёту</span>
                <i className="hv-leader__fill" />
                <span className={`hv-leader__v ${geometry.is_priceable ? '' : 'hv-bad'}`}>
                  {geometry.is_priceable ? 'да' : 'нет — сетка не замкнута'}
                </span>
              </li>
            </ul>
          )}

          <Field label="Название">
            <input
              className="hv-input"
              value={draft.title}
              onChange={(event) => {
                const title = event.target.value
                setDraft((current) => ({
                  ...current,
                  title,
                  // The slug follows the title only while creating: changing it on
                  // an existing model would break a URL somebody has shared.
                  slug: editing ? current.slug : slugify(title),
                }))
              }}
            />
          </Field>

          <Field label="Адрес (slug)" hint="Появляется в ссылке. Менять у опубликованной модели не стоит.">
            <input
              className="hv-input"
              value={draft.slug}
              disabled={Boolean(editing)}
              onChange={(event) => setDraft((c) => ({ ...c, slug: event.target.value }))}
            />
          </Field>

          <Field label="Код">
            <input
              className="hv-input"
              value={draft.code}
              onChange={(event) => setDraft((c) => ({ ...c, code: event.target.value }))}
            />
          </Field>

          <Field label="Описание">
            <textarea
              className="hv-input"
              rows={3}
              value={draft.summary}
              onChange={(event) => setDraft((c) => ({ ...c, summary: event.target.value }))}
            />
          </Field>

          <Field label="Раздел">
            <select
              className="hv-select"
              value={draft.category}
              onChange={(event) => setDraft((c) => ({ ...c, category: event.target.value }))}
            >
              {CATEGORIES.map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </Field>

          {/*
            The storefront's «Подходящие материалы» table is edited here, because
            every column of it except stock and price is a judgement somebody
            makes: how well the material suits *this* part, which one is the
            baseline, and the caveat that does not fit in a grade.
          */}
          <div className="hv-stack">
            <span className="hv-label">Подходящие материалы</span>
            <table className="hv-table">
              <thead>
                <tr>
                  <th>Код</th>
                  <th>Пригодность</th>
                  <th>Оговорка</th>
                  <th>Реком.</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {draft.materials.map((offer, index) => (
                  <tr key={index}>
                    <td>
                      <input
                        className="hv-input"
                        value={offer.code}
                        placeholder="pla"
                        onChange={(event) => patchMaterial(index, { code: event.target.value })}
                      />
                    </td>
                    <td>
                      <select
                        className="hv-select"
                        value={offer.suitability}
                        onChange={(event) =>
                          patchMaterial(index, {
                            suitability: event.target.value as Suitability,
                          })
                        }
                      >
                        {SUITABILITY.map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        className="hv-input"
                        value={offer.note}
                        placeholder="Не для улицы"
                        onChange={(event) => patchMaterial(index, { note: event.target.value })}
                      />
                    </td>
                    <td data-align="end">
                      {/*
                        Radio, not a checkbox: the recommendation is the baseline
                        every Δ price is measured against, and two baselines is
                        not a thing a reader can be shown.
                      */}
                      <input
                        type="radio"
                        name="recommended"
                        checked={offer.is_recommended}
                        onChange={() =>
                          setDraft((c) => ({
                            ...c,
                            materials: c.materials.map((entry, i) => ({
                              ...entry,
                              is_recommended: i === index,
                            })),
                          }))
                        }
                      />
                    </td>
                    <td data-align="end">
                      <button
                        type="button"
                        className="hv-btn hv-btn--sm"
                        onClick={() =>
                          setDraft((c) => ({
                            ...c,
                            materials: c.materials.filter((_, i) => i !== index),
                          }))
                        }
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button
              type="button"
              className="hv-btn hv-btn--sm"
              onClick={() =>
                setDraft((c) => ({
                  ...c,
                  materials: [
                    ...c.materials,
                    { code: '', suitability: 'good', note: '', is_recommended: false },
                  ],
                }))
              }
            >
              Добавить материал
            </button>
            <span className="hv-micro">
              Δ ЦЕНА И НАЛИЧИЕ СЧИТАЮТСЯ ИЗ СКЛАДА — ЗДЕСЬ НЕ ЗАДАЮТСЯ
            </span>
          </div>

          <Field label="Автор">
            <input
              className="hv-input"
              value={draft.author}
              onChange={(event) => setDraft((c) => ({ ...c, author: event.target.value }))}
            />
          </Field>

          {/*
            The size class is not here on purpose: it is derived from the mesh, and
            a field that let an editor type "мелкая" for a 220 mm tray would make
            the storefront's size facet disagree with the part it filters.
          */}
          <div className="hv-stack">
            <span className="hv-label">Оценки · 0–10</span>
            {BARS.map(([key, label]) => (
              <div className="hv-spec" key={key}>
                <span className="hv-spec__k">{label}</span>
                <input
                  type="range"
                  className="hv-range"
                  min={0}
                  max={10}
                  value={draft.bars[key]}
                  onChange={(event) =>
                    setDraft((c) => ({
                      ...c,
                      bars: { ...c.bars, [key]: Number(event.target.value) },
                    }))
                  }
                />
                <span className="hv-spec__v">{draft.bars[key]}</span>
              </div>
            ))}
          </div>

          <label className="hv-check">
            <input
              type="checkbox"
              checked={draft.multicolor}
              onChange={(event) => setDraft((c) => ({ ...c, multicolor: event.target.checked }))}
            />
            <span className="hv-check__body">Рассчитана на несколько цветов</span>
          </label>

          <label className="hv-check">
            <input
              type="checkbox"
              checked={draft.is_published}
              onChange={(event) => setDraft((c) => ({ ...c, is_published: event.target.checked }))}
            />
            <span className="hv-check__body">Показывать в витрине</span>
          </label>
        </Modal>
      )}
    </div>
  )
}
