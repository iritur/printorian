import { useCallback, useEffect, useMemo, useState } from 'react'

import { api, useChrome } from '@printorian/ui'

import { Preview, hours, money } from './modelCard'
import type { MeasuredPrint } from './modelCard'
import type { Locale } from '@printorian/ui'

import { ModelViewer } from './ModelViewer'
import type { ViewAngle } from './ModelViewer'

/**
 * The model library.
 *
 * The screen's whole claim is that its headline numbers are **measured** — time
 * and price from the last real print, not volume × coefficient. A model nobody
 * has printed says so rather than showing a prediction dressed as a fact; see
 * `measured` below, and ADR-0007, whose rule against inventing data this is.
 */

interface SuitableMaterial {
  code: string
  name: string
  suitability: 'excellent' | 'good' | 'limited'
  /** A caveat shown in place of the grade — «Не для улицы». */
  note: string
  is_recommended: boolean
  /** `null` when it cannot be stated: no baseline, or no measured print. */
  price_delta: string | null
  /** `null` when the shop does not carry the material at all — not zero. */
  stock_grams: string | null
}

interface ModelHistory {
  /** `null` until at least one print has finished. */
  success_rate: string | null
  finished_prints: number
  /** `null` until at least one identified customer has ordered it. */
  repeat_share: string | null
  orders: number
}

interface PriceRung {
  quantity: number
  unit_price: string
  total: string
  lead_hours: string
  discount_percent: string
  /** First rung at a new discount tier — the kit's «ПОРОГ». */
  is_threshold: boolean
}

/**
 * A published model on its way to the configurator.
 *
 * The geometry is not carried — only the slug. The bytes live behind
 * `GET /catalog/{slug}/model`, which is content-addressed and cacheable, so the
 * configurator fetches them itself rather than this screen holding a copy of every
 * mesh a reader happened to open.
 */
export interface CatalogPick {
  slug: string
  /**
   * Where the geometry is, when it is not the catalogue's own.
   *
   * The account screen re-orders a customer's *own* upload through this same
   * handoff, and that file is not reachable at `/catalog/{slug}/model` — the
   * catalogue publishes what the farm chose to publish and refuses to serve
   * somebody's private upload. Absent for a catalogue model, which is found by
   * slug as before.
   */
  href?: string
  /** Names the file in the model well until the server's own name arrives. */
  code: string
  title: string
  /**
   * The family the farm recommends for this part, when it has said so.
   *
   * `null` rather than a guess: a model with no recommendation should open on the
   * configurator's own default, not on whichever material happened to sort first.
   */
  material: string | null
}

interface CatalogCard {
  id: string
  slug: string
  code: string
  title: string
  summary: string
  category: string
  size_class: string
  difficulty: number
  strength: number
  accuracy: number
  speed: number
  supports: number
  postprocessing: number
  author: string
  multicolor: boolean
  tags: string[]
  materials: string[]
  suitable_materials: SuitableMaterial[]
  price_ladder: PriceRung[]
  price_basis: string
  history: ModelHistory
  volume_cm3: string
  width_mm: string
  depth_mm: string
  height_mm: string
  triangle_count: number
  surface_area_cm2: string
  is_watertight: boolean
  mesh_warnings: string[]
  has_geometry: boolean
  created_at: string | null
  rating: string
  rating_count: number
  print_count: number
  /** `null` until the farm has actually printed one. Never filled from a guess. */
  measured: MeasuredPrint | null
  preview: Record<string, unknown>
  license: string
  version: string
  published_at: string | null
}

interface FacetCount {
  value: string
  count: number
}

interface CatalogTable {
  rows: CatalogCard[]
  total: number
  counts: Record<string, FacetCount[]>
}

/** The eight keys, in the kit's order, with the label each one prints. */
const SORTS: { key: string; label: string }[] = [
  { key: 'popular', label: 'Популярность' },
  { key: 'price', label: 'Цена' },
  { key: 'time', label: 'Время печати' },
  { key: 'volume', label: 'Объём' },
  { key: 'difficulty', label: 'Сложность' },
  { key: 'rating', label: 'Оценка' },
  { key: 'prints', label: 'Напечатано раз' },
  { key: 'date', label: 'Новизна' },
]

/**
 * Which way a key opens.
 *
 * Cost-like ascending, quality-like descending — the first click shows what the
 * reader is looking for. Mirrors `DESCENDING_BY_DEFAULT` on the server; the
 * server is authoritative, and this exists so the arrow is right before the
 * first response comes back.
 */
const OPENS_DESCENDING = new Set(['popular', 'rating', 'prints', 'date'])

/**
 * The five facet groups, in the kit's own order and wording.
 *
 * Material is second, not last, and every label is the kit's — including the ones
 * that carry a fact: «До 50 мм» states a threshold, and `size_class_of` on the
 * server uses that number because a label naming a boundary has to be true.
 *
 * Material options are the kit's list rather than whatever the response happens
 * to contain, so a chip that reads zero still tells the reader the shop knows
 * about that material. Counts come from the API.
 */
const FACETS: { group: string; title: string; options: { value: string; label: string }[] }[] = [
  {
    group: 'cat',
    title: 'Назначение',
    options: [
      { value: 'func', label: 'Функциональные' },
      { value: 'case', label: 'Корпуса и боксы' },
      { value: 'mech', label: 'Механика' },
      { value: 'decor', label: 'Декор' },
      { value: 'org', label: 'Организация' },
    ],
  },
  {
    group: 'mat',
    title: 'Материал',
    options: [
      { value: 'pla', label: 'PLA' },
      { value: 'petg', label: 'PETG' },
      { value: 'asa', label: 'ASA' },
      { value: 'tpu', label: 'TPU' },
      { value: 'resin', label: 'Смола' },
    ],
  },
  {
    group: 'size',
    title: 'Размер',
    options: [
      { value: 's', label: 'До 50 мм' },
      { value: 'm', label: '50–150 мм' },
      { value: 'l', label: 'Более 150 мм' },
    ],
  },
  {
    group: 'colors',
    title: 'Цвета',
    options: [
      { value: '1', label: 'Один цвет' },
      { value: 'multi', label: 'Многоцветные' },
    ],
  },
  {
    group: 'diff',
    title: 'Сложность печати',
    options: [
      { value: 'easy', label: 'Простая · без поддержек' },
      { value: 'mid', label: 'Средняя' },
      { value: 'hard', label: 'Сложная · нужен инженер' },
    ],
  },
]

/** Short forms for the active-filter chips, where the full label will not fit. */
const CHIP_LABELS: Record<string, string> = {
  case: 'Корпуса',
  s: 'До 50 мм',
  m: '50–150 мм',
  l: 'Более 150 мм',
  easy: 'Простая',
  mid: 'Средняя',
  hard: 'Сложная',
  '1': 'Один цвет',
  multi: 'Многоцветные',
}

/**
 * The kit's three grades, as its own state tones.
 *
 * Deliberately the printer vocabulary: a material that is merely workable reads
 * the way a machine that is merely idle does, which is the point of having one
 * colour language across the system.
 */
const SUITABILITY_STATE: Record<string, string> = {
  excellent: 'idle',
  good: 'preparing',
  limited: 'paused',
}

const SUITABILITY_LABEL: Record<string, string> = {
  excellent: 'Отлично',
  good: 'Хорошо',
  limited: 'Ограниченно',
}

const PAGE = 24

const MATERIAL_LABELS: Record<string, string> = {
  pla: 'PLA',
  petg: 'PETG',
  asa: 'ASA',
  abs: 'ABS',
  tpu: 'TPU',
}

/**
 * What the headline price was measured on.
 *
 * The kit prints this under the slab — «PETG-CF · 1 ЦВЕТ · БЕЗ ОБРАБОТКИ · ОТ 10
 * ШТ» — because a price without its basis is a number the reader has to take on
 * faith, and every other figure in this system carries one.
 */
function basisLine(card: CatalogCard): string {
  const material = card.suitable_materials.find((entry) => entry.is_recommended)
  const parts = [
    material ? materialName(material) : card.materials[0]?.toUpperCase(),
    card.multicolor ? 'МНОГОЦВЕТНАЯ' : '1 ЦВЕТ',
    'БЕЗ ОБРАБОТКИ',
  ]
  return parts.filter(Boolean).join(' · ')
}

/**
 * The «Срок» column.
 *
 * Hours up to two days, then days — the kit switches at exactly the point where
 * «74 ч» stops being a duration anyone pictures.
 */
function lead(hours: string, locale: Locale): string {
  const total = Math.round(Number(hours))
  if (total <= 48) return `${total} ч`
  return `${Math.ceil(total / 24).toLocaleString(locale)} сут`
}

function materialName(entry: SuitableMaterial): string {
  return entry.name || MATERIAL_LABELS[entry.code] || entry.code.toUpperCase()
}

/**
 * The Δ column.
 *
 * A dash, not a zero, when the difference cannot be stated — there is no
 * baseline, or the model has never been printed and so has no measured mass to
 * price against. `± 0 ₽` would claim the materials cost the same.
 */
function formatDelta(value: string | null, locale: Locale): string {
  if (value === null) return '—'
  const amount = Math.round(Number(value))
  if (amount === 0) return '± 0 ₽'
  // A real minus sign rather than a hyphen: these sit in a tabular column and a
  // hyphen is visibly shorter than a plus.
  const sign = amount > 0 ? '+' : '−'
  return `${sign} ${Math.abs(amount).toLocaleString(locale)} ₽`
}

/** Rising cost is bad, falling cost is good — direction is not sentiment. */
function deltaTone(value: string | null): string | undefined {
  if (value === null) return 'hv-faint'
  const amount = Number(value)
  if (amount === 0) return 'hv-faint'
  return amount > 0 ? 'hv-bad' : 'hv-good'
}

function formatStock(grams: string | null): string {
  // Not stocked at all is different from stocked and empty, so it reads as a
  // dash rather than `0 кг`.
  if (grams === null) return '—'
  return `${(Number(grams) / 1000).toFixed(1)} кг`
}

/** Under a kilogram is not enough for a batch, and the kit marks it. */
function lowStock(grams: string | null): boolean {
  return grams !== null && Number(grams) < 1000
}

function Card({
  card,
  locale,
  list,
  onOpen,
}: {
  card: CatalogCard
  locale: Locale
  list: boolean
  onOpen: (card: CatalogCard) => void
}) {
  const measured = card.measured
  return (
    // Grid and list are the *same card* under a different class, which is what
    // lets switching keep scroll position. `hv-model--row` is the half that
    // matters: it puts the preview in a 92px column at `aspect-ratio: 1`.
    // Setting only `hv-cat--list` on the container leaves the card in column
    // layout and the preview fills the row.
    <article
      className={`hv-frame hv-model${list ? ' hv-model--row' : ''}`}
      data-model=""
      onClick={() => onOpen(card)}
    >
      <div className="hv-model__view">
        {/*
          Time is shown only when it was measured. An unprinted model gets the
          honest label instead of an estimate wearing a fact's clothes — the
          distinction this whole screen is built on.
        */}
        <span className="hv-model__tag hv-model__tag--tl">
          {measured ? hours(measured.minutes) : 'НЕ ПЕЧАТАЛАСЬ'}
        </span>
        <span className="hv-model__tag hv-model__tag--tr">
          {(card.materials[0] ?? '').toUpperCase() || '—'}
        </span>
        <Preview card={card} />
        {measured?.price && (
          <span className="hv-model__tag hv-model__tag--br">{money(measured.price, locale)}</span>
        )}
      </div>

      <div className="hv-model__body">
        <h2 className="hv-model__title">{card.title}</h2>
        <div className="hv-model__meta">
          <span>{card.code}</span>
          <span>
            {Math.round(Number(card.width_mm))} × {Math.round(Number(card.depth_mm))} ×{' '}
            {Math.round(Number(card.height_mm))} ММ
          </span>
          <span>{Number(card.volume_cm3).toFixed(1)} СМ³</span>
        </div>
      </div>

      <div className="hv-model__foot">
        <span className="hv-rate">
          {[1, 2, 3, 4, 5].map((star) => (
            <i key={star} {...(star <= Math.round(Number(card.rating)) ? { 'data-on': '' } : {})} />
          ))}
          <b>
            {card.rating_count > 0 ? Number(card.rating).toFixed(1) : '—'} · {card.print_count}
          </b>
        </span>
        <span className="hv-model__price">
          {measured?.price ? (
            <>
              {money(measured.price, locale)} <small>/ ШТ</small>
            </>
          ) : (
            <small>ЦЕНА ПО РАСЧЁТУ</small>
          )}
        </span>
      </div>
    </article>
  )
}

/**
 * The detail panel.
 *
 * The kit's popup is dense on purpose — it is where somebody decides whether to
 * order a part, and every panel answers one question they would otherwise have to
 * ask. Four of them here:
 *
 *   the 3D view      what is it, actually
 *   факт с печати    what did it cost last time — measured, or absent
 *   характеристики   how does it compare to the other one I am looking at
 *   геометрия        will it even print
 *
 * Two panels the kit shows are deliberately **not** here: the quantity ladder and
 * the per-material Δ price. Both are real pricing questions, and the honest way to
 * answer them is a quote from the pricing engine rather than a table of numbers
 * this screen made up. The catalogue's remaining gaps are in docs/DESIGN-KIT.md.
 */
function Detail({
  card: summary,
  locale,
  onClose,
  onConfigure,
}: {
  card: CatalogCard
  locale: Locale
  onClose: () => void
  /** «Настроить и заказать» — the popup's whole purpose. */
  onConfigure: (pick: CatalogPick) => void
}) {
  /**
   * The grid's card, replaced by the full one as soon as it arrives.
   *
   * `GET /catalog` returns what a card needs; the popup asks more — the
   * suitable-materials table joins pricing and stock, which is work no grid of
   * twenty-four should pay for. Opening on the summary means the panel is never
   * blank while that second request is in flight.
   */
  // Tagged with the slug it describes, so opening a second model never shows the
  // first model's table under the second one's name — which clearing inside the
  // effect did for exactly one render.
  const [fetched, setFetched] = useState<{ slug: string; detail: CatalogCard } | null>(null)
  const full = fetched?.slug === summary.slug ? fetched.detail : null
  const card = full ?? summary

  useEffect(() => {
    let alive = true
    const slug = summary.slug
    api
      .get<CatalogCard>(`/catalog/${slug}`)
      .then((detail) => alive && setFetched({ slug, detail }))
      // The summary is already on screen and correct as far as it goes, so a
      // failed enrichment loses the extra table rather than the whole popup.
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [summary.slug])

  const measured = card.measured
  const [angle, setAngle] = useState<ViewAngle>('iso')
  const [spin, setSpin] = useState(false)

  // Esc closes, as it does in the nav overlay. A modal that traps you is worse
  // than one that is hard to open.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const geometryUrl = card.has_geometry ? `/api/catalog/${card.slug}/model` : null

  /*
    What the configurator needs to pick this model up. Built from the *full* card,
    which is why both buttons below sit inside the popup: the grid's summary has no
    materials table, so it could not say which one the farm recommends.
  */
  const pick = (): CatalogPick => ({
    slug: card.slug,
    code: card.code,
    title: card.title,
    material: card.suitable_materials.find((entry) => entry.is_recommended)?.name ?? null,
  })

  const bars: [string, number][] = [
    ['Сложность', card.difficulty],
    ['Прочность', card.strength],
    ['Точность', card.accuracy],
    ['Скорость', card.speed],
    ['Поддержки', card.supports],
    ['Постобработка', card.postprocessing],
  ]

  return (
    <div
      className="hv-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={card.title}
      onClick={(event) => event.target === event.currentTarget && onClose()}
    >
      <div className="hv-modal hv-modal--wide">
        <div className="hv-chrome hv-chrome--static">
          <div className="hv-chrome__row">
            <span className="hv-tab">Модель :: {card.code}</span>
            <div className="hv-meta">
              <span>
                ВЕРСИЯ :: <strong>{card.version || '—'}</strong>
              </span>
              <i className="hv-meta__sep" />
              <span>
                ЛИЦЕНЗИЯ :: <strong>{card.license || '—'}</strong>
              </span>
            </div>
            <div className="hv-os">
              <span className="hv-os__label">
                {card.print_count > 0 ? `НАПЕЧАТАНА ${card.print_count} РАЗ` : 'НЕ ПЕЧАТАЛАСЬ'}
              </span>
              <button className="hv-os__x" type="button" onClick={onClose} aria-label="Закрыть">
                ✕
              </button>
            </div>
          </div>
          <div className="hv-path">
            <div className="hv-path__crumbs">
              C:/PRINTORIAN
              <span className="hv-path__here">
                /CATALOG/{card.category.toUpperCase()}/{card.code}
              </span>
            </div>
            <div className="hv-path__status">
              ГЕОМЕТРИЯ :: <b>{card.is_watertight ? 'ГЕРМЕТИЧНА' : 'НЕ ГЕРМЕТИЧНА'}</b>
            </div>
          </div>
        </div>

        <div className="hv-modal__body hv-stack">
          <div className="hv-cols hv-cols--2">
            {/* ---------------------------------------------------- viewport */}
            <div className="hv-stack">
              <div className="hv-frame hv-frame--wide" style={{ padding: 'var(--hv-2)' }}>
                <div className="hv-model__view hv-model__view--tall">
                  <span className="hv-model__tag hv-model__tag--tl">
                    {angle === 'iso' ? 'ИЗОМЕТРИЯ' : angle === 'top' ? 'СВЕРХУ' : 'СПЕРЕДИ'} · 1:1
                  </span>
                  <ModelViewer url={geometryUrl} angle={angle} spin={spin} />
                  <span className="hv-model__tag hv-model__tag--br">
                    {Number(card.width_mm).toFixed(1)} × {Number(card.depth_mm).toFixed(1)} ×{' '}
                    {Number(card.height_mm).toFixed(1)} ММ
                  </span>
                </div>
              </div>

              <div className="hv-row">
                <span className="hv-seg" role="group" aria-label="Ракурс">
                  {(
                    [
                      ['iso', 'Изометрия'],
                      ['top', 'Сверху'],
                      ['front', 'Спереди'],
                    ] as [ViewAngle, string][]
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      className="hv-seg__btn"
                      aria-pressed={angle === value}
                      onClick={() => setAngle(value)}
                    >
                      {label}
                    </button>
                  ))}
                </span>
                <button
                  type="button"
                  className="hv-btn hv-btn--sm"
                  aria-pressed={spin}
                  onClick={() => setSpin((current) => !current)}
                >
                  {spin ? 'Стоп' : 'Вращать'}
                </button>
                <span className="hv-spacer" />
                <span className="hv-micro">
                  {card.triangle_count.toLocaleString(locale)} ТРЕУГОЛЬНИКОВ
                </span>
              </div>

              {geometryUrl && (
                <div className="hv-row">
                  <a className="hv-btn hv-btn--sm" href={geometryUrl} download={`${card.code}.stl`}>
                    Скачать STL
                  </a>
                  <span className="hv-micro">ПЕРЕТАСКИВАНИЕ — ПОВОРОТ · КОЛЕСО — МАСШТАБ</span>
                </div>
              )}

              {card.suitable_materials.length > 0 && (
                <section className="hv-panel">
                  <div className="hv-panel__head">
                    <span>Подходящие материалы</span>
                  </div>
                  <div className="hv-panel__body--none">
                    <table className="hv-table">
                      <thead>
                        <tr>
                          <th>Материал</th>
                          <th>Пригодность</th>
                          <th data-align="end">Δ цена</th>
                          <th>Наличие</th>
                        </tr>
                      </thead>
                      <tbody>
                        {card.suitable_materials.map((entry) => (
                          <tr key={entry.code}>
                            <td>
                              {/*
                                The recommended row is the baseline every Δ below
                                is measured against, so it says so rather than
                                leaving the reader to infer it from a zero.
                              */}
                              {entry.is_recommended ? <b>{materialName(entry)}</b> : materialName(entry)}
                              {entry.is_recommended && (
                                <span className="hv-micro"> · РЕКОМЕНДОВАН</span>
                              )}
                            </td>
                            <td>
                              {/*
                                A caveat replaces the grade when there is one —
                                «Не для улицы» says more than «Ограниченно». The
                                grade still drives the tone.
                              */}
                              <span className="hv-state" data-state={SUITABILITY_STATE[entry.suitability]}>
                                {entry.note || SUITABILITY_LABEL[entry.suitability]}
                              </span>
                            </td>
                            <td data-align="end" className={deltaTone(entry.price_delta)}>
                              {formatDelta(entry.price_delta, locale)}
                            </td>
                            <td className={lowStock(entry.stock_grams) ? 'hv-warn' : undefined}>
                              {formatStock(entry.stock_grams)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
            </div>

            {/* ------------------------------------------------------- facts */}
            <div className="hv-stack">
              <div>
                <h2 className="hv-h hv-h--lead">{card.title}</h2>
                {card.summary && (
                  <p className="hv-prose" style={{ marginTop: 'var(--hv-2)' }}>
                    {card.summary}
                  </p>
                )}
              </div>

              {/*
                One panel, as the kit has it: what the last real print cost, and
                the geometry it was measured from. Splitting them read as two
                unrelated lists of numbers about the same object.

                The head is inverted because this is the panel the reader came
                for — the kit uses that weight exactly once per screen.
              */}
              <section className="hv-panel">
                <div className="hv-panel__head hv-panel__head--invert">
                  <span>{measured ? 'Факт с последней печати' : 'Ещё не печаталась'}</span>
                  {measured && (
                    <span className="hv-panel__aside" style={{ color: 'inherit' }}>
                      {measured.printer_name} · {new Date(measured.at).toLocaleDateString(locale)}
                    </span>
                  )}
                </div>
                <div className="hv-panel__body hv-panel__body--tight">
                  <ul className="hv-leaders">
                    {measured && (
                      <>
                        <li className="hv-leader">
                          <span className="hv-leader__k">Время печати · 1 шт</span>
                          <i className="hv-leader__fill" />
                          <span className="hv-leader__v">{hours(measured.minutes)}</span>
                        </li>
                        <li className="hv-leader">
                          <span className="hv-leader__k">Расход материала</span>
                          <i className="hv-leader__fill" />
                          <span className="hv-leader__v">
                            {Number(measured.grams).toFixed(1)} г
                          </span>
                        </li>
                      </>
                    )}
                    <li className="hv-leader">
                      <span className="hv-leader__k">Объём модели</span>
                      <i className="hv-leader__fill" />
                      <span className="hv-leader__v">{Number(card.volume_cm3).toFixed(2)} см³</span>
                    </li>
                    <li className="hv-leader">
                      <span className="hv-leader__k">Габарит</span>
                      <i className="hv-leader__fill" />
                      <span className="hv-leader__v">
                        {Number(card.width_mm).toFixed(1)} × {Number(card.depth_mm).toFixed(1)} ×{' '}
                        {Number(card.height_mm).toFixed(1)} мм
                      </span>
                    </li>
                    {Number(card.surface_area_cm2) > 0 && (
                      <li className="hv-leader">
                        <span className="hv-leader__k">Площадь поверхности</span>
                        <i className="hv-leader__fill" />
                        <span className="hv-leader__v">
                          {Number(card.surface_area_cm2).toFixed(1)} см²
                        </span>
                      </li>
                    )}
                    <li className="hv-leader">
                      <span className="hv-leader__k">Треугольников</span>
                      <i className="hv-leader__fill" />
                      <span className="hv-leader__v">
                        {card.triangle_count.toLocaleString(locale)}
                      </span>
                    </li>
                    <li className="hv-leader" data-tone={card.is_watertight ? 'good' : 'bad'}>
                      <span className="hv-leader__k">Герметичность</span>
                      <i className="hv-leader__fill" />
                      <span className="hv-leader__v">
                        {card.is_watertight ? 'проверено' : 'не замкнута'}
                      </span>
                    </li>
                    {card.mesh_warnings.length > 0 && (
                      <li className="hv-leader" data-tone="warn">
                        <span className="hv-leader__k">Замечания сетки</span>
                        <i className="hv-leader__fill" />
                        <span className="hv-leader__v">{card.mesh_warnings.length} шт</span>
                      </li>
                    )}
                  </ul>

                  {!measured && (
                    // No estimate stands in. The catalogue's numbers are
                    // measurements or they are absent; a prediction printed beside
                    // the measured rows above would be indistinguishable from one.
                    <p className="hv-hint" style={{ marginTop: 'var(--hv-3)' }}>
                      Эту модель ещё не печатали, поэтому срок и цена здесь не показаны — каталог
                      показывает только измеренное. Точный расчёт даёт конфигуратор.
                    </p>
                  )}

                  <hr className="hv-hr hv-hr--heavy" />

                  {/*
                    «От», because this is the cheapest the part has actually gone
                    out at — one colour, no finishing. The line beneath says which
                    configuration that was, so the number has a stated basis rather
                    than being a price the reader has to trust.
                  */}
                  {measured?.price ? (
                    <>
                      <div className="hv-slab hv-slab--lg">
                        <span>От</span>
                        <span className="hv-slab__v">{money(measured.price, locale)} / шт</span>
                      </div>
                      <p className="hv-micro" style={{ margin: 'var(--hv-2) 0 0' }}>
                        {basisLine(card)}
                      </p>
                    </>
                  ) : (
                    <div className="hv-slab hv-slab--lg">
                      <span>Цена</span>
                      <span className="hv-slab__v">по расчёту</span>
                    </div>
                  )}
                </div>
              </section>

              {/*
                The point of the whole popup. Outside the panel and full width, so
                it reads as the next step rather than one more fact.
              */}
              <button
                type="button"
                className="hv-btn hv-btn--primary hv-btn--lg hv-btn--block"
                onClick={() => onConfigure(pick())}
                // A model with no stored geometry cannot be quoted: there is
                // nothing to measure. The entry stays readable — its measurements
                // and history are still facts — but this is the one thing it
                // cannot do.
                disabled={!card.has_geometry}
              >
                Настроить и заказать
              </button>

              <section className="hv-panel">
                <div className="hv-panel__head">
                  <span>Характеристики печати</span>
                  <span className="hv-panel__aside">ОЦЕНКА 0–10</span>
                </div>
                <div className="hv-panel__body">
                  {bars.map(([label, value]) => (
                    <div className="hv-spec" key={label}>
                      <span className="hv-spec__k">{label}</span>
                      <span className="hv-spec__bar" style={{ ['--v' as string]: value / 10 }} />
                      <span className="hv-spec__v">{value || '—'}</span>
                    </div>
                  ))}
                </div>
              </section>

              {card.price_ladder.length > 0 && (
                <section className="hv-panel">
                  <div className="hv-panel__head">
                    <span>Цена по количеству</span>
                    <span className="hv-panel__aside">{card.price_basis}</span>
                  </div>
                  <div className="hv-panel__body--none">
                    <table className="hv-table">
                      <thead>
                        <tr>
                          <th>Количество</th>
                          <th data-align="end">За штуку</th>
                          <th data-align="end">Итого</th>
                          <th data-align="end">Срок</th>
                        </tr>
                      </thead>
                      <tbody>
                        {card.price_ladder.map((rung) => {
                          const discount = Number(rung.discount_percent)
                          return (
                            // `aria-selected` on the threshold row: the kit marks
                            // the quantity worth ordering *up to*, and that is a
                            // selection state rather than a colour.
                            <tr key={rung.quantity} aria-selected={rung.is_threshold || undefined}>
                              <td>
                                {rung.is_threshold ? (
                                  <b>{rung.quantity} шт</b>
                                ) : (
                                  `${rung.quantity} шт`
                                )}
                                {discount > 0 && (
                                  <span className="hv-micro">
                                    {' '}
                                    · {rung.is_threshold ? 'ПОРОГ ' : ''}−{discount}%
                                  </span>
                                )}
                              </td>
                              <td data-align="end">{money(rung.unit_price, locale)}</td>
                              <td data-align="end">{money(rung.total, locale)}</td>
                              <td data-align="end">{lead(rung.lead_hours, locale)}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className="hv-panel__foot">
                    <span>ЦЕНА ЗАВИСИТ ОТ МАТЕРИАЛА И ОБРАБОТКИ</span>
                    <button
                      type="button"
                      className="hv-mono"
                      onClick={() => onConfigure(pick())}
                      style={{
                        color: 'inherit',
                        background: 'none',
                        border: 0,
                        cursor: 'pointer',
                      }}
                    >
                      РАССЧИТАТЬ ›
                    </button>
                  </div>
                </section>
              )}

              <section className="hv-panel">
                <div className="hv-panel__head">
                  <span>История модели</span>
                  <span className="hv-panel__aside">
                    {card.print_count > 0 ? `${card.print_count} ПЕЧАТЕЙ` : 'НОВАЯ'}
                  </span>
                </div>
                <div className="hv-panel__body">
                  <ul className="hv-leaders">
                    {card.published_at && (
                      <li className="hv-leader">
                        <span className="hv-leader__k">Добавлена</span>
                        <i className="hv-leader__fill" />
                        <span className="hv-leader__v">
                          {new Date(card.published_at).toLocaleDateString(locale)}
                        </span>
                      </li>
                    )}
                    {card.history.success_rate !== null && (
                      // Toned by the figure itself: anything under 90% is a part
                      // the farm is quietly losing money on, and the reader
                      // deciding whether to order it should see that.
                      <li
                        className="hv-leader"
                        data-tone={Number(card.history.success_rate) >= 90 ? 'good' : 'warn'}
                      >
                        <span className="hv-leader__k">Удачных печатей</span>
                        <i className="hv-leader__fill" />
                        <span className="hv-leader__v">
                          {Number(card.history.success_rate).toFixed(1)}%{' '}
                          <small>из {card.history.finished_prints}</small>
                        </span>
                      </li>
                    )}
                    <li className="hv-leader">
                      <span className="hv-leader__k">Средняя оценка</span>
                      <i className="hv-leader__fill" />
                      <span className="hv-leader__v">
                        {card.rating_count > 0
                          ? `${Number(card.rating).toFixed(1)} из 5 · ${card.rating_count}`
                          : 'нет оценок'}
                      </span>
                    </li>
                    {card.history.repeat_share !== null && (
                      <li className="hv-leader">
                        <span className="hv-leader__k">Повторных заказов</span>
                        <i className="hv-leader__fill" />
                        <span className="hv-leader__v">
                          {Number(card.history.repeat_share).toFixed(0)}%{' '}
                          <small>из {card.history.orders}</small>
                        </span>
                      </li>
                    )}
                    <li className="hv-leader">
                      <span className="hv-leader__k">Материалы</span>
                      <i className="hv-leader__fill" />
                      <span className="hv-leader__v">
                        {card.materials.map((m) => MATERIAL_LABELS[m] ?? m.toUpperCase()).join(' · ')}
                      </span>
                    </li>
                    {card.author && (
                      <li className="hv-leader">
                        <span className="hv-leader__k">Автор</span>
                        <i className="hv-leader__fill" />
                        <span className="hv-leader__v">{card.author}</span>
                      </li>
                    )}
                  </ul>
                </div>
              </section>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export function CatalogPage({
  locale,
  onConfigure,
}: {
  locale: Locale
  /**
   * To the configurator — with a model when the reader picked one, and without
   * when they chose «Загрузить свою» and are bringing their own.
   */
  onConfigure: (pick?: CatalogPick) => void
}) {
  const [text, setText] = useState('')
  const [debounced, setDebounced] = useState('')
  const [picked, setPicked] = useState<Record<string, Set<string>>>({})
  const [sort, setSort] = useState('popular')
  const [descending, setDescending] = useState<boolean | null>(null)
  const [list, setList] = useState(false)
  const [limit, setLimit] = useState(PAGE)
  const [table, setTable] = useState<CatalogTable | null>(null)

  /*
    The kit's `CATALOG.MODELS[146]`, as a labelled pair like every other item in
    the strip. `null` while the first page is in flight — a count that starts at
    nought and jumps is worse than one that arrives.
  */
  useChrome(
    table ? { meta: [{ label: 'CATALOG.MODELS', value: String(table.total) }] } : null,
  )
  const [open, setOpen] = useState<CatalogCard | null>(null)
  const [failed, setFailed] = useState(false)

  // Typing must not fire a request per keystroke, and must not lag behind the
  // reader either. 250ms is the usual compromise.
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(text), 250)
    return () => clearTimeout(timer)
  }, [text])

  /**
   * One query string carrying search, facets, sort and paging together.
   *
   * The server takes them in a single request precisely so that sorting cannot
   * clear a filter and searching cannot ignore the facets. Assembling them here
   * in one place is the client side of that promise.
   */
  const query = useMemo(() => {
    const params = new URLSearchParams()
    if (debounced.trim()) params.set('q', debounced.trim())
    for (const [group, values] of Object.entries(picked)) {
      for (const value of values) params.append(group, value)
    }
    params.set('sort', sort)
    if (descending !== null) params.set('desc', String(descending))
    params.set('limit', String(limit))
    return params.toString()
  }, [debounced, picked, sort, descending, limit])

  useEffect(() => {
    let alive = true
    api
      .get<CatalogTable>(`/catalog?${query}`)
      .then((next) => {
        if (!alive) return
        // Cleared here rather than at the top of the effect: a search that
        // succeeds clears the previous failure, and one still in flight leaves
        // the last answer on screen instead of blanking it on every keystroke.
        setFailed(false)
        setTable(next)
      })
      .catch(() => alive && setFailed(true))
    return () => {
      alive = false
    }
  }, [query])

  const toggle = useCallback((group: string, value: string) => {
    setPicked((current) => {
      const next = new Set(current[group] ?? [])
      if (next.has(value)) next.delete(value)
      else next.add(value)
      // A new selection means a new result set, so paging starts over rather than
      // keeping a depth that belonged to a different query.
      return { ...current, [group]: next }
    })
    setLimit(PAGE)
  }, [])

  const chooseSort = useCallback(
    (key: string) => {
      // Clicking the active key flips it — the table-header gesture, already
      // learned elsewhere in this app. A different key opens its own way.
      if (key === sort) setDescending((current) => !(current ?? OPENS_DESCENDING.has(key)))
      else {
        setSort(key)
        setDescending(null)
      }
      setLimit(PAGE)
    },
    [sort],
  )

  const countFor = (group: string, value: string) =>
    table?.counts[group]?.find((entry) => entry.value === value)?.count ?? 0

  const rows = table?.rows ?? []
  const total = table?.total ?? 0
  /** Every ticked box, flattened — the kit shows these as removable chips. */
  const active = Object.entries(picked).flatMap(([group, values]) =>
    [...values].map((value) => ({ group, value })),
  )
  const labelFor = (group: string, value: string) =>
    CHIP_LABELS[value] ??
    FACETS.find((facet) => facet.group === group)?.options.find((o) => o.value === value)?.label ??
    value

  return (
    <div className="hv-cols hv-cols--2l">
      {/* ======================================================== facets */}
      <aside className="hv-sticky hv-stack">
        <section className="hv-panel">
          <div className="hv-panel__head">
            <span>Фильтры</span>
            <span className="hv-panel__aside">{total} МОДЕЛЕЙ</span>
          </div>

          {FACETS.map((facet) => (
            <div className="hv-facet" key={facet.group}>
              <div className="hv-facet__h">
                <span>{facet.title}</span>
              </div>
              <div className="hv-facet__b">
                {facet.options.map((option) => (
                  <label className="hv-facet__opt" key={option.value}>
                    <span>
                      <input
                        type="checkbox"
                        checked={picked[facet.group]?.has(option.value) ?? false}
                        onChange={() => toggle(facet.group, option.value)}
                      />{' '}
                      {option.label}
                    </span>
                    <span className="hv-facet__n">{countFor(facet.group, option.value)}</span>
                  </label>
                ))}
              </div>
            </div>
          ))}

          <div className="hv-panel__foot">
            <span>ЛОКАЛЬНАЯ БИБЛИОТЕКА ФЕРМЫ</span>
          </div>
        </section>

        {/*
          The way out of the catalogue. A reader who does not find their part
          should not have to go looking for the upload field — the kit puts it
          directly under the filters, where the search has just failed.
        */}
        <section className="hv-frame">
          <span className="hv-label">Своя модель</span>
          <p
            className="hv-prose"
            style={{ fontSize: 'var(--hv-size-small)', margin: 'var(--hv-2) 0 var(--hv-3)' }}
          >
            Нет подходящей? Загрузите STL или 3MF — расчёт цены займёт около секунды.
          </p>
          <button
            type="button"
            className="hv-btn hv-btn--primary hv-btn--block"
            // No argument: this is the reader who has their own file.
            onClick={() => onConfigure()}
          >
            Загрузить свою
          </button>
        </section>
      </aside>

      {/* ========================================================== list */}
      <div className="hv-stack">
        <div className="hv-frame hv-frame--wide">
          <span className="hv-micro">
            ЛОКАЛЬНАЯ БИБЛИОТЕКА · {total} МОДЕЛЕЙ · ВСЕ ПРОВЕРЕНЫ НА ПЕЧАТЬ
          </span>
          <h1 className="hv-display hv-display--rule" style={{ marginTop: 'var(--hv-2)' }}>
            Каталог
          </h1>
          <p className="hv-prose" style={{ marginTop: 'var(--hv-3)' }}>
            Каждая модель в этом списке хотя бы раз напечатана у нас. Указанные время и цена — не
            оценка по объёму, а факт с последней печати на реальной машине.
          </p>
        </div>

        <div className="hv-stack hv-stack--2">
          <div className="hv-row">
            <input
              className="hv-input"
              type="search"
              value={text}
              onChange={(event) => {
                setText(event.target.value)
                setLimit(PAGE)
              }}
              placeholder="ПОИСК :: НАЗВАНИЕ, КОД, НАЗНАЧЕНИЕ"
              aria-label="Поиск по каталогу"
              style={{ flex: '1 1 260px' }}
            />
            <span className="hv-seg" role="group" aria-label="Вид">
              <button
                type="button"
                className="hv-seg__btn"
                aria-pressed={!list}
                onClick={() => setList(false)}
              >
                Плитка
              </button>
              <button
                type="button"
                className="hv-seg__btn"
                aria-pressed={list}
                onClick={() => setList(true)}
              >
                Список
              </button>
            </span>
          </div>

          <div className="hv-sortbar">
            <span className="hv-micro" style={{ marginRight: 'var(--hv-2)' }}>
              СОРТИРОВКА
            </span>
            {SORTS.map((entry) => {
              const on = entry.key === sort
              const down = on ? (descending ?? OPENS_DESCENDING.has(entry.key)) : false
              return (
                <button
                  key={entry.key}
                  type="button"
                  className="hv-sort"
                  aria-pressed={on}
                  onClick={() => chooseSort(entry.key)}
                >
                  {entry.label} <span className="hv-sort__dir">{on ? (down ? '▼' : '▲') : ''}</span>
                </button>
              )
            })}
            <span className="hv-spacer" />
            <span className="hv-micro">ПОВТОРНЫЙ КЛИК МЕНЯЕТ НАПРАВЛЕНИЕ</span>
          </div>

          <div className="hv-row">
            <span className="hv-micro">
              НАЙДЕНО :: <b>{rows.length}</b> ИЗ {total}
            </span>
            {/*
              Every ticked box, as a chip that removes itself. Without this the
              only record of a narrowed result is five checkboxes in a sidebar the
              reader has already scrolled past.
            */}
            <span className="hv-row" style={{ gap: 'var(--hv-2)' }}>
              {active.map(({ group, value }) => (
                <button
                  key={`${group}:${value}`}
                  type="button"
                  className="hv-chip"
                  onClick={() => toggle(group, value)}
                  aria-label={`Снять фильтр «${labelFor(group, value)}»`}
                >
                  {labelFor(group, value)}
                </button>
              ))}
            </span>
          </div>
        </div>

        {failed && <p className="hv-hint">Каталог сейчас недоступен.</p>}

        <div className={list ? 'hv-cat hv-cat--list' : 'hv-cat'}>
          {rows.map((card) => (
            <Card key={card.id} card={card} locale={locale} list={list} onOpen={setOpen} />
          ))}
        </div>

        {!failed && rows.length === 0 && (
          <div
            className="hv-frame"
            style={{ textAlign: 'center', padding: 'var(--hv-7)' }}
          >
            <div className="hv-h">Ничего не найдено</div>
            <p className="hv-micro" style={{ marginTop: 'var(--hv-2)' }}>
              ИЗМЕНИТЕ ЗАПРОС ИЛИ СНИМИТЕ ЧАСТЬ ФИЛЬТРОВ
            </p>
          </div>
        )}

        <div className="hv-panel__foot" style={{ border: '1px solid var(--hv-line)' }}>
          <span>
            ПОКАЗАНО {rows.length} ИЗ {total}
          </span>
          {rows.length < total && (
            <button
              type="button"
              className="hv-btn hv-btn--sm"
              onClick={() => setLimit((current) => current + PAGE)}
            >
              Показать ещё
            </button>
          )}
        </div>
      </div>

      {open && (
        <Detail
          card={open}
          locale={locale}
          onClose={() => setOpen(null)}
          onConfigure={onConfigure}
        />
      )}
    </div>
  )
}
