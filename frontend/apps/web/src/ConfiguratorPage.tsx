import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiClient, ApiError } from '@printorian/api-client'
import type { Breakdown, Delta, Locale } from '@printorian/ui'
import { DeltaPreview, PriceBreakdown, translate, translateError } from '@printorian/ui'

import type { CatalogPick } from './CatalogPage'
import { ColourStep } from './ColourStep'
import { FinishStep, RUSH_KEY, finishKey } from './FinishStep'
import type { OptionDeltas } from './FinishStep'
import { MaterialStep } from './MaterialStep'
import { ModelWell } from './ModelWell'
import { SizeStep } from './SizeStep'
import type { DiscountTier } from './SizeStep'
import {
  DEFAULT_FINISH,
  FINISHES,
  chosenSpecs,
  coloursFor,
  families,
  pricingSpec,
  resizeColours,
  type Config,
  type Material,
} from './config'

const api = new ApiClient({ baseUrl: '/api' })

/**
 * How long the pointer must rest on an option before its price is previewed.
 *
 * Long enough that crossing a row of buttons on the way to one of them costs
 * nothing, short enough that it still feels like a response to hovering rather
 * than a delay. Each preview is a full model upload, so the requests skipped
 * here are the reason the answer arrives quickly when it is wanted.
 */
const HOVER_SETTLE_MS = 120

/** The kit's four numbered panels. */
const STEPS = 4

/**
 * One option being considered, as form fields.
 *
 * Values may repeat — `to_colors` and `to_finishes` are lists on the server —
 * so an array becomes several fields of the same name rather than one joined
 * string, which is what FastAPI's `list[str]` form parsing expects.
 */
type OptionChange = Partial<Record<string, string | string[]>>

/** The colours a plate would carry, as hex, for one configuration. */
function coloursAsHex(config: Config, catalogue: Material[]): string[] {
  return chosenSpecs(config, catalogue).map((material) => material.color_hex)
}

/**
 * The option fields describing a configuration.
 *
 * Both halves, always: the colour set *and* the material a quote would be priced
 * from. Changing either can move the price on its own, and a preview that
 * reported only one would understate what the customer is about to accept.
 */
function asChange(config: Config, catalogue: Material[]): OptionChange {
  const specs = chosenSpecs(config, catalogue)
  return {
    to_colors: specs.map((material) => material.color_hex),
    // Every product on the plate, not just the one that prices it: whether the
    // farm has to buy something in depends on all of them.
    to_material_codes: specs.map((material) => material.code),
  }
}

/**
 * Put a colour in one slot.
 *
 * The number of colours is chosen in step 02 and this must not change it: a
 * swatch that added or removed an entry moved the count under the customer, so
 * picking a colour silently re-answered the previous question.
 *
 * The slot the customer clicked is the only one that changes. An earlier version
 * swapped when the colour was already held elsewhere — meaning to keep every
 * slot distinct — but setting two slots to white then sent the first slot's
 * white back to where the white came from, so colours appeared to cycle round
 * the row instead of being set. Duplicates are allowed instead: two slots of one
 * colour is a plate the farm can print, the server prices it by *distinct*
 * colours so nobody is charged for a purge that never happens, and it is what
 * the customer asked for.
 */
function assignColour(colors: string[], slot: number, colour: string): string[] {
  const next = [...colors]
  next[slot] = colour
  return next
}

interface QuoteResponse {
  model: {
    model_filename: string
    /** The content address. What makes this exact geometry findable again. */
    model_sha256: string
    triangle_count: number
    volume_cm3: string
    bounding_box_mm: { x: string; y: string; z: string }
    estimated_minutes: string
    estimated_grams: string
    mesh_warnings: string[]
    promised_hours: string
    rush_hours: string
  }
  breakdown: Breakdown
  discount_tiers: DiscountTier[]
}

/**
 * What the window chrome shows while a quote is on screen.
 *
 * The kit fills the chrome's meta strip with `QUOTE`, `RATES` and `MESH`, and its
 * own path strip ends in `QUOTE.LIVE`. `AppShell` already owns that row, so the
 * page reports the facts rather than drawing them: they are what a support
 * conversation needs and a customer never reads.
 *
 * There is no `QUOTE` id here because the system has none — a quote is not
 * persisted, only an order is. The mesh digest is the honest substitute: it is the
 * key the plate cache and the print jobs are matched on, so it is what actually
 * makes this screen's state reproducible.
 */
export interface QuoteChrome {
  fileName: string
  sha256: string
  rateSnapshotId: string
}

export interface CheckoutHandoff {
  config: Config
  model: {
    fileName: string
    estimated_minutes: string
    estimated_grams: string
    /** So the checkout's foot can state the promise before an order exists. */
    promised_hours: string
  }
  breakdown: Breakdown
  /**
   * The filament choices resolved against the catalogue.
   *
   * Done here rather than at checkout because this is where the catalogue is:
   * checkout would otherwise have to fetch every material again purely to turn
   * spec codes into the colours and the priced material the order needs.
   */
  materialCode: string
  colors: string[]
}

/**
 * The scenario's steps 1–4: choose a model, configure it, see a transparent price,
 * and see what each option would change *before* committing.
 *
 * Two rules shape this component:
 *
 * 1. **It never computes a price.** Every figure comes from the server, which runs
 *    the one pricing engine (ADR-0002). The client's job is to render.
 * 2. **The delta preview asks the same engine.** Hovering an option calls
 *    `/pricing/preview`, which prices the alternative and subtracts — so what the
 *    preview promises is exactly what checkout will charge.
 *
 * Laid out as the kit does: the model and the four choice panels on the left, the
 * price on the right in a column that stays put while the left side scrolls. The
 * price is the thing the customer is watching, so it does not scroll away from
 * the control that changes it.
 */
export function ConfiguratorPage({
  locale,
  onCheckout,
  onCatalog,
  onQuoteChrome,
  fromCatalog,
}: {
  locale: Locale
  onCheckout: (handoff: CheckoutHandoff) => void
  /** Sends the customer to the catalogue to pick a published model instead. */
  onCatalog?: () => void
  /**
   * A model the customer chose in the catalogue, to be quoted instead of an upload.
   *
   * The geometry is fetched here rather than handed over, because the catalogue
   * screen never had the bytes — only a slug. Content-addressed and cacheable, so
   * a reader who opened the popup has usually already pulled it for the 3D view.
   */
  fromCatalog?: CatalogPick | null
  /** Reports the quote's identifiers for the window chrome. `null` clears them. */
  onQuoteChrome?: (chrome: QuoteChrome | null) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [materials, setMaterials] = useState<Material[]>([])
  const [config, setConfig] = useState<Config>({
    material: '',
    colors: [],
    quantity: 1,
    scale: '1',
    finishes: [DEFAULT_FINISH],
    rush: false,
  })

  const [quote, setQuote] = useState<QuoteResponse | null>(null)
  /**
   * A blob URL for the chosen file, so the 3D view can read it without a
   * round trip. Revoked when the file changes: a blob URL left open pins the
   * whole upload in memory for the life of the tab.
   */
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [delta, setDelta] = useState<Delta | null>(null)
  /** What the pointer is on, for the live frame's «ПРИ ВЫБОРЕ ::». */
  const [hovered, setHovered] = useState<string | null>(null)
  /** A price change per finish and for rush, so every button carries its figure. */
  const [optionDeltas, setOptionDeltas] = useState<OptionDeltas>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  /** Which panel the customer last worked on, for «ШАГ n / 4». */
  const [step, setStep] = useState(1)
  // Which colour slot the swatch row fills. Read through `slot` below, never
  // directly — this value can outlive the slot it names.
  const [activeSlot, setActiveSlot] = useState(0)

  // Previews are fired on every hover/toggle, so a slow one must never overwrite a
  // newer answer. Each request carries a sequence number and stale ones are dropped.
  const previewSeq = useRef(0)

  useEffect(() => {
    api
      .get<{ rows: Material[] }>('/materials')
      .then((table) => {
        setMaterials(table.rows)
        // Open on the first family and its first colour, so the initial quote
        // describes something the shop actually sells.
        setConfig((current) => {
          if (current.material) return current
          // The farm's own recommendation for this part, when it came from the
          // catalogue and the shop still carries the family. Otherwise the first
          // family, so the opening quote describes something actually for sale.
          const wanted = fromCatalog?.material
          const offered = families(table.rows)
          const family = (wanted && offered.includes(wanted) ? wanted : offered[0]) ?? ''
          const colour = coloursFor(family, table.rows)[0]?.color_name ?? ''
          return { ...current, material: family, colors: colour ? [colour] : [] }
        })
      })
      .catch((exc: unknown) => setError(describe(exc, locale)))
    // `fromCatalog` is deliberately not a dependency: it seeds the opening choice
    // and must not re-run the catalogue fetch, nor overwrite a family the customer
    // has since chosen for themselves.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locale])

  /**
   * Pull a catalogue model in as though the customer had uploaded it.
   *
   * A `File`, not a special case: everything downstream — the quote, the previews,
   * the 3D view, the checkout handoff — already works on one, and a second path
   * for "catalogue models" would be a second set of bugs. The only difference is
   * where the bytes came from.
   */
  useEffect(() => {
    if (!fromCatalog) return
    let live = true

    void (async () => {
      setBusy(true)
      setError(null)
      try {
        const response = await fetch(`/api/catalog/${fromCatalog.slug}/model`)
        if (!response.ok) throw new ApiError(response.status, await response.json())
        const bytes = await response.blob()
        if (!live) return
        const named = new File([bytes], fileNameOf(response, fromCatalog), {
          type: 'model/stl',
        })
        setFile(named)
        setPreviewUrl((current) => {
          if (current) URL.revokeObjectURL(current)
          return URL.createObjectURL(named)
        })
        // The quote itself waits for the material catalogue — see `priced` below.
        // Both arrive from separate requests and either can be second.
        priced.current = false
        setStep(1)
      } catch (exc: unknown) {
        if (live) setError(describe(exc, locale))
      } finally {
        if (live) setBusy(false)
      }
    })()

    return () => {
      live = false
    }
  }, [fromCatalog, locale])

  const requestQuote = useCallback(
    // The file is a parameter, not read from state. When called straight after
    // `setFile`, the state has not applied yet, so a closure over it would still be
    // null and the quote would silently never happen.
    async (next: Config, source: File | null = file) => {
      if (!source || !next.material || next.colors.length === 0) return
      setBusy(true)
      setError(null)
      try {
        const form = toForm(source, next, materials)
        const response = await fetch('/api/pricing/quote', { method: 'POST', body: form })
        if (!response.ok) throw new ApiError(response.status, await response.json())
        setQuote((await response.json()) as QuoteResponse)
        setDelta(null)
      } catch (exc: unknown) {
        setError(describe(exc, locale))
        setQuote(null)
      } finally {
        setBusy(false)
      }
    },
    [file, locale, materials],
  )

  const askPreview = useCallback(
    async (change: OptionChange, source: File, from: Config) => {
      const form = toForm(source, from, materials)
      for (const [key, value] of Object.entries(change)) {
        if (value === undefined) continue
        // An array becomes repeated fields, not a joined string: the server
        // reads `to_colors` and `to_finishes` as lists.
        for (const entry of Array.isArray(value) ? value : [value]) form.append(key, entry)
      }
      const response = await fetch('/api/pricing/preview', { method: 'POST', body: form })
      if (!response.ok) return null
      return ((await response.json()) as { delta: Delta }).delta
    },
    [materials],
  )

  const sendPreview = useCallback(
    async (change: OptionChange, label: string | null) => {
      if (!file || !quote) return
      // Nothing to compare against means the server would reject it as
      // `error.pricing.no_option_change`; do not spend a round trip finding out.
      if (Object.keys(change).length === 0) return
      const ticket = ++previewSeq.current
      try {
        const answer = await askPreview(change, file, config)
        // Drop an answer that a later request — or a pointer leaving the panel —
        // has already superseded.
        if (answer && ticket === previewSeq.current) {
          setDelta(answer)
          setHovered(label)
        }
      } catch {
        // A failed preview is not worth interrupting the customer for; the quote
        // itself is unaffected and the next interaction will try again.
      }
    },
    [file, quote, config, askPreview],
  )

  /**
   * Every option's price change, at rest.
   *
   * The kit prints a figure on all four finish buttons and on rush at once, which
   * is the page's whole argument — the customer sees what painting costs without
   * having to hover to find out. So each is a real comparison from the engine,
   * fetched once per quote rather than computed here.
   *
   * The currently selected option is skipped: comparing a configuration with
   * itself is a round trip whose answer is known to be zero.
   */
  useEffect(() => {
    if (!file || !quote) {
      setOptionDeltas({})
      return
    }
    let live = true
    const wanted: { key: string; change: OptionChange }[] = [
      ...FINISHES.filter((code) => !config.finishes.includes(code)).map((code) => ({
        key: finishKey(code),
        change: { to_finishes: code } as OptionChange,
      })),
      { key: RUSH_KEY, change: { to_rush: String(!config.rush) } },
    ]

    void Promise.all(
      wanted.map(async (entry) => {
        try {
          return [entry.key, await askPreview(entry.change, file, config)] as const
        } catch {
          return [entry.key, null] as const
        }
      }),
    ).then((answers) => {
      // A configuration change that lands mid-flight invalidates all of them: the
      // deltas describe a comparison against the configuration they were asked
      // about, so keeping any would label a button with the wrong figure.
      if (live) setOptionDeltas(Object.fromEntries(answers))
    })

    return () => {
      live = false
    }
  }, [file, quote, config, askPreview])

  /**
   * Whether the model on screen has been priced yet.
   *
   * A quote needs a mesh *and* a material, and those arrive from two independent
   * requests — the upload (or the catalogue fetch) and `/materials`. Either can be
   * second, so neither can be the one that fires the quote. This fires it when the
   * pair is complete, once per model.
   */
  const priced = useRef(false)

  useEffect(() => {
    if (priced.current || !file || !config.material || config.colors.length === 0) return
    priced.current = true
    void requestQuote(config, file)
  }, [file, config, requestQuote])

  const hoverTimer = useRef<number | null>(null)

  /**
   * Preview the option the pointer has settled on.
   *
   * Debounced because each preview re-uploads the model and the server re-runs
   * the mesh analysis: sweeping across four finish buttons on the way to one of
   * them used to fire four full uploads, and the answer the customer wanted
   * arrived behind three they did not.
   */
  const previewOption = useCallback(
    (change: OptionChange, label: string | null = null) => {
      if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current)
      hoverTimer.current = window.setTimeout(() => {
        hoverTimer.current = null
        void sendPreview(change, label)
      }, HOVER_SETTLE_MS)
    },
    [sendPreview],
  )

  /**
   * Stop previewing and show the selected configuration again.
   *
   * Both halves matter. Cancelling the timer stops a preview the customer has
   * already moved away from, and bumping the sequence invalidates any request
   * already in flight — without that, a slow answer lands after the pointer has
   * gone and the panel keeps describing an option nobody is looking at.
   */
  const clearPreview = useCallback(() => {
    if (hoverTimer.current !== null) {
      window.clearTimeout(hoverTimer.current)
      hoverTimer.current = null
    }
    previewSeq.current += 1
    setDelta(null)
    setHovered(null)
  }, [])

  // A pointer that leaves via an unmount must not leave a timer behind.
  useEffect(() => () => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current)
  }, [])

  useEffect(() => {
    if (!onQuoteChrome) return
    onQuoteChrome(
      quote
        ? {
            fileName: quote.model.model_filename,
            sha256: quote.model.model_sha256,
            rateSnapshotId: quote.breakdown.rate_snapshot_id ?? '',
          }
        : null,
    )
    // Cleared on the way out, or the chrome would keep describing a quote the
    // customer has navigated away from.
    return () => onQuoteChrome(null)
  }, [quote, onQuoteChrome])

  const apply = (patch: Partial<Config>, at = step) => {
    setStep(at)
    const next = { ...config, ...patch }
    setConfig(next)
    void requestQuote(next)
  }

  const chooseFile = (chosen: File | null) => {
    setFile(chosen)
    // Swap the blob URL, releasing the previous one. Without the revoke every
    // re-upload pins another copy of the mesh in memory.
    setPreviewUrl((current) => {
      if (current) URL.revokeObjectURL(current)
      return chosen ? URL.createObjectURL(chosen) : null
    })
    setStep(1)
    // Not quoted here: a file alone is not enough, and the effect above owns that
    // judgement for the catalogue path too.
    priced.current = false
  }

  const palette = coloursFor(config.material, materials)

  /**
   * Which slot the swatch row is filling.
   *
   * Clamped on read rather than corrected in an effect: choosing fewer colours,
   * or a material with a shorter palette, can drop the slot the customer was on,
   * and an effect would render one frame pointing at a slot that no longer
   * exists. This cannot be out of range.
   */
  const slot = Math.min(activeSlot, Math.max(config.colors.length - 1, 0))

  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)

  return (
    /*
      No wrapper of its own. `AppShell` renders the `<main>` landmark and the
      page's gutter — a second `<main>` here was invalid HTML (a document has one)
      and stacked a second padding on top of the shell's, so this screen sat 16px
      further in than every other one and than the kit.
    */
    <div className="hv-cols hv-cols--2">
      {/*
        Each group of options resets on leave, not the page as a whole.

        Per-*button* handlers would flicker: moving between two options fires a
        leave in the gap between them. Page-level was the other extreme — the
        preview stayed up while the pointer sat in whitespace, describing an
        option nobody was pointing at any more. The panel is the boundary that
        matches what the customer means: sweeping across the swatches is still
        choosing a colour, leaving the panel is not.
      */}
      <div className="hv-stack">
        <ModelWell
          locale={locale}
          file={file}
          previewUrl={previewUrl}
          measurements={quote?.model ?? null}
          step={step}
          steps={STEPS}
          onFile={chooseFile}
          onFromCatalog={() => onCatalog?.()}
        />

        {error && (
          <p className="hv-hint hv-bad" role="alert">
            {error}
          </p>
        )}

        <MaterialStep
          materials={materials}
          /*
            Only when the shop can actually supply it. A recommendation for a
            family nobody stocks is not a choice worth pinning the step to — the
            configurator opened on something else, and the step should describe
            what is really being priced.
          */
          pinned={
            fromCatalog?.material && families(materials).includes(fromCatalog.material)
              ? fromCatalog.material
              : null
          }
          value={config.material}
          onChange={(family) => {
            const offered = coloursFor(family, materials).map((option) => option.color_name)
            // PETG has Clear and the others do not: a colour the new material does
            // not come in has to go, or the plate names a product the shop cannot
            // supply in any state.
            const kept = config.colors.filter((colour) => offered.includes(colour))
            apply(
              {
                material: family,
                colors: kept.length > 0 ? kept : offered.slice(0, config.colors.length || 1),
              },
              1,
            )
          }}
          onPreview={(family) =>
            previewOption(asChange({ ...config, material: family }, materials), family)
          }
          onClearPreview={clearPreview}
        />

        <ColourStep
          locale={locale}
          materials={materials}
          config={config}
          slot={slot}
          onSlot={setActiveSlot}
          onCount={(count) => apply({ colors: resizeColours(config.colors, count, palette) }, 2)}
          onColour={(option) =>
            apply({ colors: assignColour(config.colors, slot, option.color_name) }, 2)
          }
          onPreviewCount={(count) =>
            previewOption(
              asChange(
                { ...config, colors: resizeColours(config.colors, count, palette) },
                materials,
              ),
              `${count} ${t('configurator.colours')}`,
            )
          }
          onPreviewColour={(option) =>
            previewOption(
              asChange(
                { ...config, colors: assignColour(config.colors, slot, option.color_name) },
                materials,
              ),
              option.color_name,
            )
          }
          onClearPreview={clearPreview}
        />

        <SizeStep
          locale={locale}
          scale={config.scale}
          quantity={config.quantity}
          tiers={quote?.discount_tiers ?? []}
          onScale={(scale) => apply({ scale }, 3)}
          onQuantity={(quantity) => apply({ quantity }, 3)}
          onPreviewQuantity={(quantity) =>
            previewOption({ to_quantity: String(quantity) }, `${quantity} ${t('unit.piece')}`)
          }
          onClearPreview={clearPreview}
        />

        <FinishStep
          locale={locale}
          config={config}
          deltas={optionDeltas}
          currency={quote?.breakdown.currency ?? 'RUB'}
          promisedHours={quote?.model.promised_hours ?? '0'}
          rushHours={quote?.model.rush_hours ?? '0'}
          onFinish={(code) => apply({ finishes: [code] }, 4)}
          onRush={(rush) => apply({ rush }, 4)}
          onPreview={(change, label) => previewOption(change, label)}
          onClearPreview={clearPreview}
        />
      </div>

      <div className="hv-sticky hv-stack" aria-busy={busy}>
        {/*
          What-changes sits *above* the breakdown. It appears and disappears with
          every hover, so putting it last made the column grow and shrink at the
          bottom — the page height changed under the pointer and the view jumped.
          Above, it also lands nearer the options being hovered, which is where
          the customer is already looking.
        */}
        {delta && (
          <DeltaPreview
            delta={delta}
            locale={locale}
            {...(hovered === null ? {} : { option: hovered })}
          />
        )}

        {quote ? (
          <>
            <PriceBreakdown
              breakdown={quote.breakdown}
              locale={locale}
              promisedHours={quote.model.promised_hours}
            />

            <button
              type="button"
              className="hv-btn hv-btn--primary hv-btn--lg hv-btn--block"
              onClick={() =>
                file &&
                onCheckout({
                  config,
                  model: {
                    fileName: file.name,
                    estimated_minutes: quote.model.estimated_minutes,
                    estimated_grams: quote.model.estimated_grams,
                    promised_hours: quote.model.promised_hours,
                  },
                  breakdown: quote.breakdown,
                  materialCode: pricingSpec(config, materials)?.code ?? '',
                  colors: coloursAsHex(config, materials),
                })
              }
            >
              {t('checkout.place')}
            </button>

            <p className="hv-micro" style={{ textAlign: 'center', margin: 0 }}>
              {t('configurator.price_lock')}
            </p>
          </>
        ) : (
          !error && <p className="hv-hint">{t('configurator.upload_prompt')}</p>
        )}
      </div>
    </div>
  )
}

/**
 * What to call a mesh pulled from the catalogue.
 *
 * The server sends the file's own name in `Content-Disposition`, which is the one
 * the farm stored and the one the customer will see again on the order. The
 * catalogue code is the fallback — never the model title, which is prose and makes
 * a poor filename.
 */
function fileNameOf(response: Response, pick: CatalogPick): string {
  const header = response.headers.get('Content-Disposition') ?? ''
  const quoted = /filename="([^"]+)"/.exec(header)
  return quoted?.[1] || `${pick.code}.stl`
}

function toForm(file: File, config: Config, catalogue: Material[]): FormData {
  const form = new FormData()
  form.append('model', file)
  // The engine prices one material; the dearest chosen filament keeps a quote
  // from landing under what the plate actually costs. See `pricingFilament`.
  const specs = chosenSpecs(config, catalogue)
  // `material_code` stays for a single-colour plate; `material_codes` carries
  // the whole set so the server can price and check stock across all of them.
  form.append('material_code', pricingSpec(config, catalogue)?.code ?? '')
  for (const spec of specs) form.append('material_codes', spec.code)
  form.append('quantity', String(config.quantity))
  form.append('scale', config.scale)
  form.append('rush', String(config.rush))
  // Real colours, as hex. The fleet reports an AMS slot's colour the same way,
  // so an order's colours and a printer's loaded colours are finally the same
  // vocabulary rather than `colour-1` against `#FFFFFF`.
  for (const colour of coloursAsHex(config, catalogue)) form.append('colors', colour)
  for (const finish of config.finishes) form.append('finishes', finish)
  return form
}

function describe(exc: unknown, locale: Locale): string {
  if (exc instanceof ApiError) return translateError(locale, { code: exc.code, details: exc.details })
  return translate(locale, 'error.internal')
}
