import type { ReactNode } from 'react'

/**
 * The two kit primitives every account panel is built from.
 *
 * Local to this app rather than promoted into `@printorian/ui`. Both are styled
 * by the shared `screens.css`, so the design system already owns how they look;
 * what is here is only the small amount of markup that goes with it, and a
 * shared component with exactly one consumer is a guess about the second.
 */

/**
 * One row of a settings list: name and hint on the left, control on the right.
 *
 * The hint is not filler. Every row in the kit that has one uses it to say what
 * the setting *costs* — «Девять писем на заказ», «Отключить нельзя» — and a
 * switch whose consequence is unstated is a switch people leave alone.
 */
export function Setting({
  name,
  hint,
  changed,
  children,
}: {
  name: string
  hint?: string
  /** Draws the kit's modified marker down the left edge. */
  changed?: boolean
  children: ReactNode
}) {
  return (
    <div className="hv-set" {...(changed ? { 'data-changed': 'true' } : {})}>
      <span>
        <span className="hv-set__name">{name}</span>
        {hint && <span className="hv-set__hint">{hint}</span>}
      </span>
      <span className="hv-set__v">{children}</span>
    </div>
  )
}

/**
 * The kit's square switch.
 *
 * `role="switch"` with `aria-checked` rather than a checkbox, because the kit
 * styles a `<button>` — and a screen reader told this is a button with no state
 * would announce «Каждая смена этапа» and nothing about whether it is on.
 */
export function Switch({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string
  checked: boolean
  disabled?: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <button
      className="hv-switch"
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    />
  )
}
