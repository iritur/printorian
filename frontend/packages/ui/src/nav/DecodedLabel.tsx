import { useEffect, useState } from 'react'

/**
 * Glyphs the noise is drawn from.
 *
 * Cyrillic, digits and console punctuation — the same alphabet the interface is
 * written in, so the scramble reads as *this* console resolving rather than as
 * generic matrix rain. Copied from `design/js/menu.js`.
 */
const GLYPHS = 'АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ0123456789/\\:.·▮'

/** ~30fps. Faster reads as flicker; slower delays the label being legible. */
const FRAME_MS = 28

/** How many frames past the end, so the tail has time to resolve. */
const TAIL = 8

/** The trailing window of already-resolved characters. */
const RESOLVED = 6

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * One row's label, resolving out of noise when the row becomes current.
 *
 * The last piece of the kit's open sequence that CSS cannot express: the
 * characters are *different characters* frame to frame, not a transform of the
 * same ones, so it has to be JavaScript. Everything else in the overlay — the
 * backdrop scale, the corner brackets, the scan pass, the 34ms entry stagger,
 * the preview's flicker and the route diagram drawing itself — is keyframes in
 * `menu.css`, driven only by a class or a custom property.
 *
 * Three rules it must not break:
 *
 * - **Flavour never delays legibility.** Under `prefers-reduced-motion` the
 *   effect does not run at all, and the final text is what renders on the first
 *   frame. It is not slowed down; it is absent.
 * - **The accessible name never scrambles.** The noise is `aria-hidden` and the
 *   button carries a stable `aria-label`, so a screen reader announces "О ферме"
 *   throughout rather than reading four hundred milliseconds of garbage.
 * - **Spaces stay spaces.** Scrambling them turns a two-word label into one
 *   long smear and loses the word count the eye is using to track the row.
 */
export function DecodedLabel({ text, active }: { text: string; active: boolean }) {
  /*
    Only the *noise* is state, and it is tagged with the label it belongs to.
    Everything else falls out of that: with no scramble in flight the component
    renders `text` directly, so the reduced-motion path and the label changing
    mid-flight both need no state write at all — where they used to need one, a
    render late. It also retires the ref that existed to guard the second case.
  */
  const [noise, setNoise] = useState<{ label: string; frame: string } | null>(null)
  const shown = noise?.label === text ? noise.frame : text

  useEffect(() => {
    if (!active || prefersReducedMotion()) return

    let frame = 0
    const total = text.length + TAIL

    const timer = setInterval(() => {
      frame += 1
      if (frame > total) {
        clearInterval(timer)
        setNoise(null)
        return
      }

      let out = ''
      for (let i = 0; i < text.length; i += 1) {
        if (text[i] === ' ') {
          out += ' '
        } else if (i < frame - RESOLVED) {
          out += text[i]
        } else if (i < frame) {
          out += GLYPHS[Math.floor(Math.random() * GLYPHS.length)]
        } else {
          // Not yet reached. A space rather than a glyph, so the label grows out
          // of the left edge instead of appearing as a full-width block of noise.
          out += ' '
        }
      }
      setNoise({ label: text, frame: out })
    }, FRAME_MS)

    return () => {
      clearInterval(timer)
      // Whatever interrupted this — a new active row, an unmount, a filter
      // keystroke — must not leave a half-resolved label frozen on screen.
      setNoise(null)
    }
  }, [active, text])

  return <span aria-hidden="true">{shown}</span>
}
