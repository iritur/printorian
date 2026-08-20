import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

/**
 * The kit's vertical section rail, with the marker that travels between rows.
 *
 * `harvester/tabs.css` has carried the styling since the kit was ported; this is
 * the behaviour it was waiting for. Two pieces, and both matter:
 *
 * **The marker is one element, not a border per row.** A left border that blinks
 * off one row and on at another reads as two controls. One bar that slides tells
 * you they are the same control in two states, which is the whole reason the kit
 * draws it that way — so it is measured from the selected row and moved, rather
 * than re-rendered.
 *
 * **The panel replays its entry animation on every switch.** `is-entering` is
 * added on change and removed on `animationend`; without the removal the class
 * stays applied and the animation only ever plays once, on first paint.
 */

export interface TabRailProps<Key extends string> {
  tabs: readonly { key: Key; label: string }[]
  current: Key
  onSelect: (key: Key) => void
  /** Accessible name for the rail. */
  label: string
}

export function TabRail<Key extends string>({
  tabs,
  current,
  onSelect,
  label,
}: TabRailProps<Key>) {
  const rail = useRef<HTMLElement | null>(null)
  const [mark, setMark] = useState<{ y: number; h: number } | null>(null)

  /*
    Measured from the DOM rather than computed from an index and a row height.
    The rows are not a fixed height — a long section name wraps — and a marker
    positioned by arithmetic drifts on exactly the screens where it is noticed.
  */
  const measure = useCallback(() => {
    const host = rail.current
    if (!host) return
    const selected = host.querySelector<HTMLElement>('[aria-selected="true"]')
    if (!selected) return
    setMark({ y: selected.offsetTop, h: selected.offsetHeight })
  }, [])

  useEffect(measure, [measure, current, tabs])

  useEffect(() => {
    // Fonts land after first paint and change the row height with them, which
    // would otherwise leave the marker a few pixels short until the next switch.
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [measure])

  return (
    <nav
      ref={rail}
      className="hv-tree hv-rail"
      style={{ padding: 'var(--hv-2) 0' }}
      role="tablist"
      aria-label={label}
    >
      {tabs.map((tab) => (
        <button
          key={tab.key}
          className="hv-tree__item"
          type="button"
          role="tab"
          aria-selected={tab.key === current}
          onClick={() => onSelect(tab.key)}
        >
          {tab.label}
          {tab.key === current && <span className="hv-nav__chev">›</span>}
        </button>
      ))}
      <span
        className="hv-rail__mark"
        data-ready={mark !== null}
        style={{ '--y': `${mark?.y ?? 0}px`, '--h': `${mark?.h ?? 0}px` } as React.CSSProperties}
      />
    </nav>
  )
}

/**
 * The panel the rail switches, animated on every change.
 *
 * Keyed on `name`, so React replaces the subtree rather than reconciling it.
 * Two panels made of `.hv-panel` elements reconcile *very* well, which would
 * mean the section changed with no animation at all and no way to tell whether
 * anything had happened. The key is also what re-arms the entry state, since a
 * remounted component starts from its initial state again.
 */
export function TabView({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="hv-tabview">
      <Panel key={name} name={name}>
        {children}
      </Panel>
    </div>
  )
}

/** How long the kit's entry sequence takes, plus room for a slow first paint. */
const ENTRY_MS = 600

function Panel({ name, children }: { name: string; children: ReactNode }) {
  const [entering, setEntering] = useState(true)

  /*
    A timer, not only `animationend`.

    `animationend` is the precise signal and it is used — but it is not
    guaranteed to arrive. A page that is not compositing (a background tab, a
    headless check) never runs the animation and never fires it, and
    `prefers-reduced-motion` turns the whole thing off by design. In any of those
    the class would stay applied forever.

    Nothing visible breaks when it does — the entry keyframes end at the
    element's natural state and the scan overlay ends transparent — but a class
    named `is-entering` that is permanently true is a lie the next person reading
    this has to work out for themselves.
  */
  useEffect(() => {
    const timer = window.setTimeout(() => setEntering(false), ENTRY_MS)
    return () => window.clearTimeout(timer)
  }, [])

  return (
    <div
      data-tab-panel={name}
      className={`hv-stack${entering ? ' is-entering' : ''}`}
      onAnimationEnd={(event) => {
        // The panel's own animation, not the staggered children's — clearing on
        // the first of those would cut the sequence short. `currentTarget` is
        // the panel by definition, so this compares against it rather than
        // against a ref that has to be kept in step with the element.
        if (event.target === event.currentTarget) setEntering(false)
      }}
    >
      {children}
    </div>
  )
}
