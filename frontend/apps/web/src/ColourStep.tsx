import { translate } from '@printorian/ui'
import type { Locale } from '@printorian/ui'

import { MAX_COLORS, coloursFor, isInStock, specFor } from './config'
import type { Config, Material } from './config'

/**
 * «02 :: Цвет» — how many colours, and which.
 *
 * Three controls, because there are three questions and the kit asks them in this
 * order:
 *
 *   count    how many colours the plate carries — a real cost, since each change
 *            makes the machine purge the nozzle
 *   slots    which of those positions is being filled
 *   palette  what goes in it
 *
 * The slot grid is the piece that was missing before. Without it the swatch row
 * had nowhere to put a colour, so picking one grew the list and silently
 * re-answered the count question above it.
 */

export interface ColourStepProps {
  locale: Locale
  materials: Material[]
  config: Config
  /** Which slot the palette fills. Already clamped by the caller. */
  slot: number
  onSlot: (slot: number) => void
  onCount: (count: number) => void
  onColour: (option: Material) => void
  /** Hover/focus preview, so a choice is priced before it is made. */
  onPreviewCount: (count: number) => void
  onPreviewColour: (option: Material) => void
  onClearPreview: () => void
}

export function ColourStep({
  locale,
  materials,
  config,
  slot,
  onSlot,
  onCount,
  onColour,
  onPreviewCount,
  onPreviewColour,
  onClearPreview,
}: ColourStepProps) {
  const t = (key: Parameters<typeof translate>[1], details?: Record<string, unknown>) =>
    translate(locale, key, details)

  // Colours offered in the chosen material. PETG has Clear and the others do
  // not, so the row follows the material rather than being one global list.
  const palette = coloursFor(config.material, materials)

  return (
    <section className="hv-panel" onMouseLeave={onClearPreview}>
      <div className="hv-panel__head">
        <span>02 :: {t('configurator.colour_slot')}</span>
        <span className="hv-panel__aside">{t('configurator.ams_slots', { count: MAX_COLORS })}</span>
      </div>
      <div className="hv-panel__body hv-stack">
        {/*
          How many colours. A segmented control rather than a list: there are only
          four possibilities and they are read at a glance.
        */}
        <div className="hv-row">
          <span className="hv-label" style={{ margin: 0 }}>
            {t('configurator.colours')}
          </span>
          {/*
            The leave handler is on the *group*, not only on the panel.

            Panel-level alone was a real bug: the pointer resting in the panel's
            whitespace between the segment and the palette kept a preview on screen
            describing an option nobody was pointing at. Per-*button* was the other
            extreme and flickered, because moving between two buttons fires a leave
            in the gap. The group is the boundary that matches what the customer
            means - sweeping across the swatches is still choosing a colour.
          */}
          <span
            className="hv-seg"
            role="group"
            aria-label={t('configurator.colours')}
            onMouseLeave={onClearPreview}
          >
            {Array.from({ length: MAX_COLORS }, (_, index) => index + 1).map((count) => (
              <button
                key={count}
                type="button"
                className="hv-seg__btn"
                aria-pressed={config.colors.length === count}
                // Each extra colour costs a purge when the machine swaps filament
                // mid-plate, so this is a real number rather than a free choice.
                onMouseEnter={() => onPreviewCount(count)}
                onFocus={() => onPreviewCount(count)}
                onBlur={onClearPreview}
                onClick={() => onCount(count)}
              >
                {count}
              </button>
            ))}
          </span>
          <span className="hv-hint">{t('configurator.purge_hint')}</span>
        </div>

        {/*
          The slots, as the kit's AMS cards.

          The kit prints «AMS.A1 · 820 Г В СЛОТЕ» under each one. That is a
          *fleet* fact — which spool sits in which slot of which machine — and the
          machine is not chosen until the order is paid (it is dispatched, not
          picked). So the card names the slot and the product, and does not claim a
          bay or a weight the shop cannot know yet.
        */}
        <div className="hv-grid hv-grid--2">
          {config.colors.map((colour, index) => {
            const spec = specFor(config.material, colour, materials)
            return (
              <button
                // Slots are positions, not values: two may hold the same colour
                // for a moment while the customer rearranges them.
                key={index}
                type="button"
                className="hv-slot"
                aria-pressed={index === slot}
                style={{ '--hv-swatch': spec?.color_hex } as React.CSSProperties}
                onClick={() => onSlot(index)}
              >
                <span className="hv-slot__chip" />
                <span className="hv-slot__body">
                  <span className="hv-h">
                    {t('configurator.colour_slot')} {index + 1} :: {colour}
                  </span>
                  <span className="hv-slot__n">
                    {spec ? spec.name.toUpperCase() : t('configurator.slot_hint', { slot: index + 1 })}
                    {spec && !isInStock(spec) && ' · ПОД ЗАКАЗ'}
                  </span>
                </span>
              </button>
            )
          })}
        </div>

        <div>
          <span className="hv-label">{t('configurator.palette')}</span>
          <div
            className="hv-swatches"
            style={{ marginTop: 'var(--hv-2)' }}
            role="group"
            aria-label={t('configurator.palette')}
            onMouseLeave={onClearPreview}
          >
            {palette.map((option) => {
              const usedAt = config.colors.indexOf(option.color_name)
              return (
                <button
                  key={option.color_name}
                  type="button"
                  className="hv-swatch"
                  // Pressed means "this is the colour of the slot being filled",
                  // not "this colour is somewhere on the plate" — the row has to
                  // answer the question the slot grid above it asks.
                  aria-pressed={usedAt === slot}
                  // Marks a colour another slot already holds. Not a warning —
                  // picking it is allowed and prices as one filament — but the
                  // customer should be able to see that they are about to repeat
                  // themselves rather than discover it from the plate.
                  data-used={usedAt !== -1 && usedAt !== slot}
                  aria-label={option.color_name}
                  title={option.color_name}
                  style={{ '--hv-swatch': option.color_hex } as React.CSSProperties}
                  onMouseEnter={() => onPreviewColour(option)}
                  onFocus={() => onPreviewColour(option)}
                  onBlur={onClearPreview}
                  onClick={() => onColour(option)}
                />
              )
            })}
          </div>
        </div>
      </div>
    </section>
  )
}
