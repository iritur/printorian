import { createContext, useContext, useEffect } from 'react'

/**
 * What a screen puts in the window chrome's status strip.
 *
 * The kit gives every screen three facts and a clock up there, and they are not
 * decoration: they are the identifiers a support conversation and a log line use
 * — a quote id, a rate snapshot, how many models the catalogue holds. Which
 * three depends entirely on the screen, and only the screen knows them.
 *
 * Hence a context rather than a prop. `AppShell` sits above every page and the
 * facts live inside them, so the alternative was a bespoke `onXChrome` callback
 * threaded through the shell for each screen in turn — there were two already,
 * and seven more were coming.
 */

export interface MetaItem {
  label: string
  value: string
}

/**
 * What a screen contributes to the window chrome.
 *
 * Both halves are optional and both fall back to the shell's own props when a
 * screen does not report them. The path is here beside the meta because it has
 * the same shape of problem: the configurator's tail says `QUOTE.LIVE` only
 * while there *is* a live quote, and only the configurator knows that.
 */
export interface Chrome {
  meta?: MetaItem[]
  /** The path strip, without the `C:/PRINTORIAN` root the shell adds. */
  path?: string
}

/** Set by `AppShell`. `null` restores whatever the shell was given as default. */
const ChromeContext = createContext<((chrome: Chrome | null) => void) | null>(null)

export const ChromeProvider = ChromeContext.Provider

/**
 * Report this screen's chrome for as long as it is mounted.
 *
 * Cleared on unmount, which is the whole reason it is a hook and not a setter:
 * a screen that navigated away must not leave its quote id in the title bar of
 * the next one.
 *
 * Pass `null` while the facts are not known yet — mid-fetch, or before a file
 * has been uploaded. The strip then shows the shell's own default rather than a
 * row of placeholders, which is the same "measured or absent" rule the rest of
 * the system follows.
 */
export function useChrome(chrome: Chrome | null): void {
  const set = useContext(ChromeContext)

  /*
    Keyed on the contents, not the object.

    Callers build these inline — `{ meta: [{ label: 'MESH', value: name }] }` —
    so it is a new object on every render, and depending on it directly would set
    state on every render, which sets state again. The serialised contents change
    only when a fact does.
  */
  const key =
    chrome === null
      ? ''
      : `${chrome.path ?? ''}|${(chrome.meta ?? [])
          .map((item) => `${item.label} ${item.value}`)
          .join('')}`

  useEffect(() => {
    set?.(chrome)
    return () => set?.(null)
    // `chrome` is deliberately absent: `key` is its content, and including the
    // object would defeat the whole point of computing one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, set])
}
