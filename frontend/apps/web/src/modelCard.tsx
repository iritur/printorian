import type { Locale } from '@printorian/ui'

/**
 * The parts a catalogue model's card is drawn from, shared by the two screens
 * that draw one.
 *
 * The catalogue page and the promo page's teaser render the same `hv-model`
 * card from the same fields, and before this they had two copies of the
 * formatting between them — which is how «2 ч 21 м» becomes «2ч21м» on one
 * screen and nobody notices for a month.
 */

/** What the farm actually measured the last time it printed one. */
export interface MeasuredPrint {
  at: string
  minutes: string
  grams: string
  price: string | null
  printer_name: string
}

/** The subset of a catalogue card the shared drawing needs. */
export interface Drawable {
  /** `null` until the farm has actually printed one. Never filled from a guess. */
  measured: MeasuredPrint | null
  preview: Record<string, unknown>
}

/** `2 ч 21 м` — the kit's duration, from a count of minutes. */
export function hours(minutes: string): string {
  const total = Math.round(Number(minutes))
  const h = Math.floor(total / 60)
  const m = total % 60
  return h > 0 ? `${h} ч ${m} м` : `${m} м`
}

/** Whole roubles. Kopecks on a card are noise at this size. */
export function money(value: string, locale: Locale): string {
  return `${Math.round(Number(value)).toLocaleString(locale)} ₽`
}

/**
 * A schematic line drawing of a model.
 *
 * The kit's previews are deliberately engineering drawings rather than renders —
 * honest about a part that does not exist yet, and legible in both themes. A
 * model whose `preview` is empty still gets a shape rather than an empty frame.
 */
export function Preview({ card }: { card: Drawable }) {
  const paths = Array.isArray(card.preview?.paths) ? (card.preview.paths as string[]) : []
  return (
    <svg viewBox="0 0 200 150" aria-hidden="true">
      {paths.length > 0 ? (
        paths.map((d, index) => <path key={index} data-edge="" d={d} />)
      ) : (
        <>
          <path data-face="" d="M30 100 L100 60 L170 100 L100 140 Z" />
          <path data-edge="" d="M30 100 L100 60 L170 100 L100 140 Z" />
          <path data-edge="" d="M30 100 L30 62 L100 22 L170 62 L170 100" />
          <path data-edge="" d="M100 60 L100 22" />
        </>
      )}
    </svg>
  )
}
