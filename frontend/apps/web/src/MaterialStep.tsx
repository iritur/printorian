import { useCallback, useEffect, useMemo, useState } from 'react'

import { api } from '@printorian/ui'

import type { Material } from './config'
import { coloursFor, families } from './config'

/**
 * «01 :: Материал» — the kit's first step, and its alternatives modal.
 *
 * Two ways in, as a segmented control:
 *
 *   По сценарию      the farm chooses, from what the part is *for*
 *   Выбрать вручную  the customer chooses, from the whole catalogue
 *
 * The scenario path is not a shortcut to the same list. It calls
 * `GET /materials/recommend`, which scores every spec against hard requirements
 * — tensile strength, heat deflection, flexibility, outdoor use — and returns the
 * reasons it picked what it did. Those reasons are why the modal can explain the
 * choice instead of asserting it.
 *
 * The copy is Russian, like the rest of the design-kit integration — the kit is a
 * Russian document and `CatalogPage` carries its strings the same way. Nothing
 * here is locale-dependent yet, so the component takes no `locale`: holding one it
 * never reads would claim a translation that does not exist.
 */

/** The kit's four scenarios, and the requirements each one implies. */
const SCENARIOS = [
  {
    id: 'functional-outdoor',
    label: 'Функциональная деталь · нагрузка · улица',
    /** Sent to `/materials/recommend`. Named so the reader can see the reasoning. */
    query: { min_tensile_mpa: 40, requires_outdoor: true },
  },
  {
    id: 'prototype',
    label: 'Прототип · проверка формы',
    // Nothing is required of a shape check, so the recommendation falls back to
    // what is cheapest and actually on the shelf.
    query: {},
  },
  {
    id: 'decor',
    label: 'Декор · выставочная модель',
    query: {},
  },
  {
    id: 'engineering-heat',
    label: 'Инженерная деталь · термостойкость',
    query: { min_hdt_c: 90, min_tensile_mpa: 40 },
  },
] as const

type ScenarioId = (typeof SCENARIOS)[number]['id']

interface SpecView {
  code: string
  name: string
  family: string
  density_g_per_cm3: string
  sell_price_per_gram: string
  tensile_mpa: string | null
  hdt_c: string | null
  is_flexible: boolean
  is_outdoor_safe: boolean
  status: string
  total_remaining_grams: string
}

interface ScenarioMatch {
  spec: SpecView
  score: number
  /** Machine-readable, e.g. `match.tensile` — the wording is chosen here. */
  reasons: string[]
}

/**
 * One material family, as the configurator offers it.
 *
 * The recommender scores *specs*, and a spec is a colour: a shop carrying five
 * PETG colours returns PETG five times. But the plate is priced in a family — the
 * colour is step 02 — so the comparison has to be at family level, which is the
 * same distinction `_catalog_panels.py::_suitable_materials` already draws on the
 * server side for the catalogue's «Подходящие материалы» table.
 */
interface FamilyOffer {
  family: string
  /** The best-scoring colour. Physical properties are a family trait, so its serve. */
  spec: SpecView
  score: number
  reasons: string[]
  /** Every colour's stock summed, because the family is what is on offer. */
  grams: number
  /**
   * The dearest colour's rate.
   *
   * Not the average and not the cheapest: the customer has not picked a colour
   * yet, so any figure below the top of the range is a number the next step
   * cannot honour. Quoting the ceiling means switching family never costs more
   * than the modal said it would.
   */
  perGram: number
}

const REASONS: Record<string, string> = {
  'match.tensile': 'прочность',
  'match.hdt': 'термостойкость',
  'match.flexible': 'гибкость',
  'match.outdoor': 'стойкость к улице',
  'match.in_stock': 'есть на складе',
}

/** 0–10, from megapascals. The kit shows «ПРОЧНОСТЬ 8/10», not «45 МПа». */
function outOfTen(value: string | null, ceiling: number): string {
  if (value === null) return '—'
  return `${Math.min(10, Math.round((Number(value) / ceiling) * 10))} / 10`
}

function kilos(grams: number): string {
  return `${(grams / 1000).toFixed(1)} кг`
}

export interface MaterialStepProps {
  materials: Material[]
  /**
   * A family chosen for this specific part, rather than by this step.
   *
   * Today that is the catalogue's own recommendation, carried in when a customer
   * picks a published model. It opens the manual path, because the scenario
   * recommender is generic — it answers "what suits an outdoor load-bearing part"
   * — while the catalogue's answer is about *this* model, from a farm that has
   * printed it. Letting the generic one overwrite the specific one on arrival
   * would throw away the better answer before the customer saw it.
   *
   * They can still hand the choice back: «По сценарию» takes over from that point.
   */
  pinned?: string | null
  /** The family currently priced. */
  value: string
  onChange: (family: string) => void
  /** Hover/focus preview, so picking a material is priced before it is chosen. */
  onPreview: (family: string) => void
  onClearPreview: () => void
}

export function MaterialStep({
  materials,
  pinned,
  value,
  onChange,
  onPreview,
  onClearPreview,
}: MaterialStepProps) {
  /**
   * Which path the *customer* chose, or `null` while they have not said.
   *
   * Separate from `manual` below because the answer has two sources and only one
   * of them is a state change. Until the customer touches the control, the path
   * follows whether a family was pinned for this part; after that, their choice
   * stands whatever arrives later.
   */
  const [path, setPath] = useState<'scenario' | 'manual' | null>(null)
  const [scenario, setScenario] = useState<ScenarioId>('functional-outdoor')
  const [matches, setMatches] = useState<ScenarioMatch[] | null>(null)
  const [open, setOpen] = useState(false)

  /*
    Derived during render, not synced by an effect.

    An effect would be a frame late, and one frame is enough: the adopt-effect
    below and a `setManual` effect run in the same pass, so the adopt would still
    see the previous value and overwrite the pinned family before the flag caught
    up. Computing it here means there is no moment where the two disagree.
  */
  const manual = path === null ? Boolean(pinned) : path === 'manual'

  const chosen = SCENARIOS.find((entry) => entry.id === scenario) ?? SCENARIOS[0]

  const recommend = useCallback(async () => {
    const params = new URLSearchParams({ limit: '5' })
    for (const [key, raw] of Object.entries(chosen.query)) {
      params.set(key, String(raw))
    }
    try {
      const answer = await api.get<ScenarioMatch[]>(`/materials/recommend?${params}`)
      // Shape-checked, not just awaited. A response that is not a list would
      // otherwise throw inside the render below and take the whole configurator
      // down with it — including the price, which does not depend on this at all.
      setMatches(Array.isArray(answer) ? answer : [])
    } catch {
      // The manual path still works, so a failed recommendation costs the
      // suggestion rather than the step.
      setMatches([])
    }
  }, [chosen])

  useEffect(() => {
    if (!manual) void recommend()
  }, [manual, recommend])

  /** Colour-level matches collapsed to one row per family. */
  const alternatives = useMemo<FamilyOffer[]>(() => {
    const byFamily = new Map<string, FamilyOffer>()
    for (const match of matches ?? []) {
      const grams = Number(match.spec.total_remaining_grams)
      const perGram = Number(match.spec.sell_price_per_gram)
      const seen = byFamily.get(match.spec.family)
      if (!seen) {
        byFamily.set(match.spec.family, {
          family: match.spec.family,
          spec: match.spec,
          score: match.score,
          reasons: match.reasons,
          grams,
          perGram,
        })
        continue
      }
      seen.grams += grams
      seen.perGram = Math.max(seen.perGram, perGram)
      if (match.score > seen.score) {
        seen.spec = match.spec
        seen.score = match.score
        seen.reasons = match.reasons
      }
    }
    return [...byFamily.values()].sort((a, b) => b.score - a.score)
  }, [matches])

  const best = alternatives[0]

  /**
   * The family the inset describes.
   *
   * Not simply the top recommendation: on the manual path the customer's choice
   * is what is being priced, and labelling it «Выбрано» while showing the
   * recommender's favourite would describe a material nobody selected. Falls back
   * to nothing when the chosen family is outside the scored set, because the inset
   * exists to report measurements and there are none to report.
   */
  const shown = alternatives.find((offer) => offer.family === value) ?? (manual ? undefined : best)

  /**
   * Adopt the farm's choice.
   *
   * Only while the scenario path is active, and only when the recommendation is
   * a family the shop actually offers colours in — otherwise the plate would
   * name a product no colour exists for.
   */
  useEffect(() => {
    if (manual || !best) return
    const family = best.family
    if (family !== value && coloursFor(family, materials).length > 0) onChange(family)
  }, [manual, best, value, materials, onChange])

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>01 :: Материал</span>
        <span className="hv-panel__aside">СПОСОБ ВЫБОРА</span>
      </div>
      <div className="hv-panel__body hv-stack">
        <div className="hv-seg" role="group" aria-label="Способ выбора материала">
          <button
            type="button"
            className="hv-seg__btn"
            aria-pressed={!manual}
            onClick={() => setPath('scenario')}
          >
            По сценарию
          </button>
          <button
            type="button"
            className="hv-seg__btn"
            aria-pressed={manual}
            onClick={() => setPath('manual')}
          >
            Выбрать вручную
          </button>
        </div>

        {manual ? (
          <div className="hv-field" onMouseLeave={onClearPreview}>
            <label className="hv-label" htmlFor="cfg-material">
              Материал
            </label>
            <select
              className="hv-select"
              id="cfg-material"
              value={value}
              onChange={(event) => onChange(event.target.value)}
              onMouseEnter={() => onPreview(value)}
              onBlur={onClearPreview}
            >
              {families(materials).map((family) => (
                <option key={family} value={family}>
                  {family}
                </option>
              ))}
            </select>
            <span className="hv-hint">Одна семья материала на плиту</span>
          </div>
        ) : (
          <div className="hv-field">
            <label className="hv-label" htmlFor="cfg-scenario">
              Сценарий использования
            </label>
            <select
              className="hv-select"
              id="cfg-scenario"
              value={scenario}
              onChange={(event) => setScenario(event.target.value as ScenarioId)}
            >
              {SCENARIOS.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.label}
                </option>
              ))}
            </select>
            <span className="hv-hint">Система подберёт материал сама</span>
          </div>
        )}

        {/*
          What was picked, and on what evidence. The kit puts this in an inset
          panel so it reads as an answer rather than another control.
        */}
        {shown && (
          <div className="hv-panel" style={{ background: 'var(--hv-bg-inset)' }}>
            <div className="hv-panel__body hv-panel__body--tight hv-row hv-row--between">
              <div>
                <div className="hv-h">
                  {manual ? 'Выбрано' : 'Подобрано'} :: {shown.family} · {shown.spec.name}
                </div>
                <div className="hv-micro">
                  ПРОЧНОСТЬ {outOfTen(shown.spec.tensile_mpa, 70)} · УФ{' '}
                  {shown.spec.is_outdoor_safe ? '9 / 10' : '2 / 10'} · ТЕРМО{' '}
                  {shown.spec.hdt_c ? `${Math.round(Number(shown.spec.hdt_c))} °C` : '—'} · НА СКЛАДЕ{' '}
                  {kilos(shown.grams).toUpperCase()}
                </div>
              </div>
              {alternatives.length > 1 && (
                <button
                  type="button"
                  className="hv-btn hv-btn--sm"
                  onClick={() => setOpen(true)}
                >
                  Альтернативы · {alternatives.length - 1}
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {open && (
        <Alternatives
          scenario={chosen.label}
          scenarioCode={chosen.id.replace('-', '.').toUpperCase()}
          matches={alternatives}
          chosen={value}
          onPick={(family) => {
            // Picking by hand is a manual choice, whatever brought you here — the
            // scenario must not immediately overwrite it on the next render.
            setPath('manual')
            onChange(family)
            setOpen(false)
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </section>
  )
}

/**
 * «Материалы :: Альтернативы».
 *
 * The comparison the recommendation is hiding: every candidate on the axes the
 * scenario weighed, so a customer can disagree with the farm on evidence rather
 * than on a hunch. Δ price is against the current choice, priced per gram — the
 * plate's mass is the same whichever material it is.
 */
function Alternatives({
  scenario,
  scenarioCode,
  matches,
  chosen,
  onPick,
  onClose,
}: {
  scenario: string
  /** `FUNCTIONAL.OUTDOOR` — the chrome row is machine codes, not prose. */
  scenarioCode: string
  matches: FamilyOffer[]
  chosen: string
  onPick: (family: string) => void
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const current = matches.find((entry) => entry.family === chosen)
  const base = current ? current.perGram : null

  return (
    <div
      className="hv-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Материалы :: Альтернативы"
      onClick={(event) => event.target === event.currentTarget && onClose()}
    >
      <div className="hv-modal">
        <div className="hv-chrome hv-chrome--static">
          <div className="hv-chrome__row">
            <span className="hv-tab">Материалы :: Альтернативы</span>
            <div className="hv-meta">
              <span>
                SCENARIO :: <strong>{scenarioCode}</strong>
              </span>
            </div>
            <div className="hv-os">
              <span className="hv-os__label">{matches.length} ВАРИАНТА</span>
              <button className="hv-os__x" type="button" onClick={onClose} aria-label="Закрыть">
                ✕
              </button>
            </div>
          </div>
          <div className="hv-path">
            <div className="hv-path__crumbs">C:/PRINTORIAN/INVENTORY/RECOMMEND</div>
            <div className="hv-path__status">
              STATUS :: <b>OPEN</b>
            </div>
          </div>
        </div>

        <div className="hv-modal__body">
          <div className="hv-table-wrap">
            <table className="hv-table">
              <thead>
                <tr>
                  <th>Материал</th>
                  <th>Прочность</th>
                  <th>УФ</th>
                  <th>Термо</th>
                  <th data-align="end">Склад</th>
                  <th data-align="end">Δ Цена</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {matches.map((entry) => {
                  const isChosen = entry.family === chosen
                  const delta = base === null ? null : entry.perGram - base
                  // Under a kilo across every colour is a family that may not
                  // finish the plate — the kit tones that cell `warn`.
                  const low = entry.grams < 1000
                  return (
                    <tr key={entry.family} {...(isChosen ? {} : { 'data-activatable': '' })}>
                      <td>
                        {isChosen ? <b>{entry.family}</b> : entry.family}
                        {isChosen && <span className="hv-micro"> · ПОДОБРАНО</span>}
                      </td>
                      <td>{outOfTen(entry.spec.tensile_mpa, 70)}</td>
                      <td>{entry.spec.is_outdoor_safe ? '9 / 10' : '2 / 10'}</td>
                      <td>
                        {entry.spec.hdt_c ? `${Math.round(Number(entry.spec.hdt_c))} °C` : '—'}
                      </td>
                      <td data-align="end" className={low ? 'hv-warn' : undefined}>
                        {kilos(entry.grams)}
                      </td>
                      <td
                        data-align="end"
                        className={
                          delta === null || delta === 0
                            ? 'hv-faint'
                            : delta > 0
                              ? 'hv-bad'
                              : 'hv-good'
                        }
                      >
                        {/*
                          Per gram, not per plate. The mass is the same whichever
                          material prints it, and the configurator's own breakdown
                          is where the total lands — quoting a plate figure here
                          would be a second price for one job.
                        */}
                        {delta === null || delta === 0
                          ? '± 0 ₽/г'
                          : `${delta > 0 ? '+' : '−'} ${Math.abs(delta).toFixed(2)} ₽/г`}
                      </td>
                      <td>
                        {isChosen ? (
                          <span className="hv-state" data-state="idle">
                            Выбран
                          </span>
                        ) : (
                          <button
                            type="button"
                            className="hv-btn hv-btn--sm"
                            onClick={() => onPick(entry.family)}
                          >
                            Выбрать
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/*
            Why, in the server's own words. `reasons` are codes (ADR-0012) and the
            wording is chosen here — but the *grounds* are the recommender's, so
            this paragraph cannot drift from what it actually weighed.
          */}
          <p className="hv-prose" style={{ marginTop: 'var(--hv-3)' }}>
            {matches[0]
              ? `Подбор опирается на сценарий использования. Для «${scenario}» решающими были: ` +
                `${matches[0].reasons.map((code) => REASONS[code] ?? code).join(', ')}. ` +
                'Любой вариант можно выбрать вручную — расчёт пересчитается сразу.'
              : 'Ни один материал на складе не отвечает требованиям этого сценария.'}
          </p>
        </div>

        <div className="hv-panel__foot">
          <span>ИСТОЧНИК :: INVENTORY.RECOMMEND</span>
          <button className="hv-btn hv-btn--sm" type="button" onClick={onClose}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  )
}
