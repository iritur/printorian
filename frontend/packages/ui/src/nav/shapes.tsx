/**
 * One schematic line drawing per destination.
 *
 * Deliberately not illustration — these are route markers. Each is a handful of
 * strokes that says what *kind* of place a destination is (a queue, a grid of
 * machines, a stack of stock, a door), so the preview pane reads before the
 * prose does.
 *
 * Copied from `design/js/menu.js`. Kept as data rather than as components
 * because the overlay animates them by stroke order — `menu.css` addresses
 * `nth-child` on the children of `.hv-menu__pv-svg`.
 */
export type ShapeName = 'cube' | 'pipe' | 'grid' | 'nodes' | 'stack' | 'doc' | 'key'

export const SHAPES: Record<ShapeName, string> = {
  cube: '<path d="M20 60 L70 34 L120 60 L70 86 Z"/><path d="M20 60 L20 40 L70 14 L120 40 L120 60"/><path d="M70 34 L70 14"/>',
  pipe: '<path d="M14 50 H126"/><rect x="14" y="42" width="30" height="16"/><rect x="56" y="42" width="30" height="16"/><rect x="98" y="42" width="28" height="16"/>',
  grid: '<rect x="16" y="24" width="48" height="24"/><rect x="76" y="24" width="48" height="24"/><rect x="16" y="58" width="48" height="24"/><rect x="76" y="58" width="48" height="24"/>',
  nodes:
    '<circle cx="34" cy="34" r="12"/><circle cx="106" cy="34" r="12"/><circle cx="34" cy="74" r="12"/><circle cx="106" cy="74" r="12"/>',
  stack:
    '<path d="M70 20 L124 44 L70 68 L16 44 Z"/><path d="M16 58 L70 82 L124 58"/><path d="M16 72 L70 96 L124 72"/>',
  doc: '<path d="M40 16 H88 L104 32 V96 H40 Z"/><path d="M88 16 V32 H104"/><path d="M54 52 H90 M54 66 H90 M54 80 H76"/>',
  key: '<circle cx="46" cy="52" r="20"/><path d="M64 52 H124"/><path d="M108 52 V70 M120 52 V66"/>',
}
