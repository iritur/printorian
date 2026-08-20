import { useCallback, useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

import type { MetaItem } from './AppShell'

/**
 * The kit's popup: a window, not a drawer.
 *
 * The storefront already drew these by hand — the catalogue card, the material
 * comparison — and each copy carried a slightly different amount of the
 * behaviour. One had Esc and no focus handling; another had neither. This is
 * that markup with the behaviour attached once, so a screen that opens a popup
 * gets all of it rather than whichever parts its author remembered.
 *
 * It is deliberately the same chrome as a real screen: the `A :: B` tab, the
 * `KEY :: value` meta strip, the `C:/PRINTORIAN/...` path. A popup in this system
 * reads as *another window of the same instrument*, which is why an inline panel
 * that pushes the page around was the wrong shape for "new printer" — it makes
 * creating a machine look like editing the list.
 *
 * ## What it owns
 *
 * **Esc closes.** A modal that traps you is worse than one that is hard to open.
 *
 * **The backdrop closes.** Clicks on the overlay itself, never on a click that
 * merely *ended* there — a drag that starts inside the dialog and releases on the
 * backdrop is a text selection, not a dismissal, and closing on it loses whatever
 * was being typed.
 *
 * **Focus goes in and comes back.** On open, focus moves to the first control
 * inside; on close it returns to whatever opened it, so a keyboard user is not
 * dropped on `<body>` and made to tab from the top of the page again.
 *
 * **Tab stays inside.** `aria-modal` tells a screen reader the rest of the page
 * is inert; without a trap that promise is false for anyone tabbing.
 *
 * **The page behind does not scroll.** Otherwise a wheel over the backdrop moves
 * the list underneath, and the row you came from is gone when you close.
 */

export interface ModalProps {
  /** Left of the chrome row, in the kit's `A :: B` form — "Новый принтер :: Парк". */
  title: string
  /** The `KEY :: value` strip: the identifiers a support conversation needs. */
  meta?: MetaItem[]
  /** Right of the meta strip, before the ✕. A state, not a label. */
  status?: string
  /** The path strip, without the `C:/PRINTORIAN` root the modal adds. */
  path?: string
  /** Right-hand side of the path strip. */
  pathStatus?: string
  /** Wider variant, for the popups that carry a table or a viewport. */
  wide?: boolean
  /** Pinned to the bottom, outside the scrolling body — where the actions go. */
  footer?: ReactNode
  onClose: () => void
  children: ReactNode
  /** For tests and for a caller that needs to reach the dialog element. */
  labelledBy?: string
}

export function Modal({
  title,
  meta = [],
  status,
  path,
  pathStatus,
  wide = false,
  footer,
  onClose,
  children,
}: ModalProps) {
  const dialog = useRef<HTMLDivElement | null>(null)
  // Where focus came from, so closing puts it back rather than dropping it on
  // body. The same thing the nav overlay does with its opener.
  const opener = useRef<HTMLElement | null>(null)
  // Whether the pointer went *down* on the backdrop. A click is only a dismissal
  // when it both started and ended there.
  const fromBackdrop = useRef(false)

  const close = useCallback(() => {
    onClose()
  }, [onClose])

  useEffect(() => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const restore = opener.current
    // The first control *in the body*, not in the dialog: searching the whole
    // thing finds the ✕ in the chrome, and opening "new printer" with focus on
    // the close button is the opposite of helpful. For a form popup this is the
    // first field, which is what somebody is about to type into.
    const body = dialog.current?.querySelector('.hv-modal__body')
    const first =
      body?.querySelector<HTMLElement>(FOCUSABLE) ??
      dialog.current?.querySelector<HTMLElement>(FOCUSABLE)
    ;(first ?? dialog.current)?.focus()

    return () => restore?.focus()
  }, [])

  useEffect(() => {
    const { body } = document
    const previous = body.style.overflow
    body.style.overflow = 'hidden'
    return () => {
      body.style.overflow = previous
    }
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        close()
        return
      }
      if (event.key !== 'Tab' || !dialog.current) return

      // Hidden controls are skipped via `[hidden]`, which is how this kit hides
      // things — the reset makes it `display: none !important` precisely so it
      // cannot be overridden. Deliberately *not* `offsetParent`: it is the usual
      // visibility test and it is null for every element under jsdom, which
      // silently collapses the trap to a single element and makes it untestable.
      const focusable = [...dialog.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (element) => element.closest('[hidden]') === null,
      )
      if (focusable.length === 0) return
      const first = focusable[0] as HTMLElement
      const last = focusable[focusable.length - 1] as HTMLElement

      // Only the two ends need intervening; everything between them wraps by
      // itself, and stealing every Tab would break a `<select>`'s own handling.
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [close])

  return (
    <div
      className="hv-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onMouseDown={(event) => {
        fromBackdrop.current = event.target === event.currentTarget
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget && fromBackdrop.current) close()
        fromBackdrop.current = false
      }}
    >
      <div className={wide ? 'hv-modal hv-modal--wide' : 'hv-modal'} ref={dialog}>
        <div className="hv-chrome hv-chrome--static">
          <div className="hv-chrome__row">
            <span className="hv-tab">{title}</span>
            {meta.length > 0 && (
              <div className="hv-meta">
                {meta.map((item, index) => (
                  <span key={item.label}>
                    {index > 0 && <i className="hv-meta__sep" />}
                    {item.label} :: <strong>{item.value}</strong>
                  </span>
                ))}
              </div>
            )}
            <div className="hv-os">
              {status && <span className="hv-os__label">{status}</span>}
              <button className="hv-os__x" type="button" onClick={close} aria-label="Закрыть">
                ✕
              </button>
            </div>
          </div>
          {path && (
            <div className="hv-path">
              <div className="hv-path__crumbs">
                C:/PRINTORIAN<span className="hv-path__here">{path}</span>
              </div>
              {pathStatus && <div className="hv-path__status">{pathStatus}</div>}
            </div>
          )}
        </div>

        <div className="hv-modal__body hv-stack">{children}</div>

        {footer && <div className="hv-panel__foot">{footer}</div>}
      </div>
    </div>
  )
}

/**
 * What counts as focusable.
 *
 * `[tabindex="-1"]` is excluded on purpose: an element opted out of the tab
 * order should not be dragged back into it by a trap.
 */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
