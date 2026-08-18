import { useEffect, useState } from 'react'

import type { Locale } from '../i18n/messages'

/** The kit's two themes. Void is the default and the one it was drawn in. */
export const THEMES = ['void', 'paper'] as const
export type Theme = (typeof THEMES)[number]

const STORAGE_KEY = 'printorian.theme'

/**
 * Read the stored choice, tolerating a value nobody wrote.
 *
 * `localStorage` is shared with anything else on the origin and survives
 * versions of this app that spelled the themes differently, so an unrecognised
 * value is treated as absent rather than stamped onto the document.
 */
function stored(): Theme {
  try {
    const found = window.localStorage.getItem(STORAGE_KEY)
    return (THEMES as readonly string[]).includes(found ?? '') ? (found as Theme) : 'void'
  } catch {
    // Private browsing, or storage disabled. The default is a perfectly good
    // answer and is not worth failing a render over.
    return 'void'
  }
}

/**
 * Void / Paper, as the kit's segmented control.
 *
 * Harvester themes by `data-theme` on the root element rather than by the OS
 * colour-scheme preference. That is deliberate: the console lives on a shop
 * floor where the useful choice is "can I read this under these lights", which
 * is not what a laptop's night mode is answering.
 */
export function ThemeSwitch({ locale }: { locale: Locale }) {
  const [theme, setTheme] = useState<Theme>(stored)

  useEffect(() => {
    // Void is the stylesheet's bare `:root`, so it is the *absence* of the
    // attribute. Setting `data-theme="void"` would work today only because no
    // rule matches it, which is a coincidence rather than a contract.
    if (theme === 'void') document.documentElement.removeAttribute('data-theme')
    else document.documentElement.setAttribute('data-theme', theme)

    try {
      window.localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      // Not being able to remember the choice is not a reason to refuse it.
    }
  }, [theme])

  return (
    <span className="hv-seg" role="group" aria-label={locale === 'ru' ? 'Тема' : 'Theme'}>
      {THEMES.map((option) => (
        <button
          key={option}
          type="button"
          className="hv-seg__btn"
          aria-pressed={theme === option}
          onClick={() => setTheme(option)}
        >
          {option === 'void' ? 'Void' : 'Paper'}
        </button>
      ))}
    </span>
  )
}
