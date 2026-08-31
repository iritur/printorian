import type { KeyboardEvent } from 'react'

/**
 * Arrow-key movement across a tab list, shared by the kit's two tab shapes.
 *
 * A tab strip is not a row of buttons, and the difference is entirely here: a
 * keyboard user Tabs *to* the list once and then moves inside it with the arrow
 * keys, because the alternative is fourteen Tab presses to get past the settings
 * rail. That is what `role="tablist"` promises a screen reader, and a strip that
 * makes the promise without keeping it is worse than a plain row of buttons,
 * which at least does not lie about how it works.
 *
 * It lives in its own file so the horizontal strip and the vertical rail cannot
 * drift apart. They already differ in the axis they listen on — the pattern says
 * Left/Right for one and Up/Down for the other, and honouring only the axis that
 * matches the layout is what stops a rail from swallowing the page's own
 * horizontal scrolling.
 *
 * Selection follows focus, which is the pattern's default and the right one for
 * panels that are already rendered: arrowing along the strip shows each section
 * as you reach it, rather than making every visit cost an extra Enter.
 */
export function tablistKeyDown<Key extends string>(
  event: KeyboardEvent<HTMLElement>,
  keys: readonly Key[],
  onSelect: (key: Key) => void,
  orientation: 'horizontal' | 'vertical',
): void {
  const forward = orientation === 'horizontal' ? 'ArrowRight' : 'ArrowDown'
  const back = orientation === 'horizontal' ? 'ArrowLeft' : 'ArrowUp'

  /*
    Read from the DOM rather than from `current`, because the tab with focus is
    not always the tab that is selected — Tab lands on the selected one, and from
    there every arrow press moves focus first and selection after it.
  */
  const buttons = [...event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]')]
  const from = buttons.indexOf(document.activeElement as HTMLElement)
  if (from < 0 || buttons.length === 0) return

  let to: number
  if (event.key === forward) to = (from + 1) % buttons.length
  else if (event.key === back) to = (from - 1 + buttons.length) % buttons.length
  else if (event.key === 'Home') to = 0
  else if (event.key === 'End') to = buttons.length - 1
  else return

  const next = keys[to]
  if (next === undefined) return

  // Before the state change, deliberately: React reconciles the same button
  // element, so focus set here survives the re-render, while focusing afterwards
  // would race the render that has not happened yet.
  event.preventDefault()
  buttons[to]?.focus()
  onSelect(next)
}
