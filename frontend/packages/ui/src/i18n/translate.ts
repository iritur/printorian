import { DEFAULT_LOCALE, catalogues } from './messages'
import type { Locale, MessageKey } from './messages'

export type Details = Record<string, unknown>

/**
 * Look up a message and interpolate `{placeholders}` from `details`.
 *
 * `details` is exactly the object the backend puts in an error body, so an API
 * failure renders without any per-endpoint mapping code.
 */
export function translate(locale: Locale, key: MessageKey, details?: Details): string {
  const catalogue = catalogues[locale] ?? catalogues[DEFAULT_LOCALE]
  const template = catalogue[key]
  return details ? interpolate(template, details) : template
}

function interpolate(template: string, details: Details): string {
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = details[name]
    return value === undefined ? match : String(value)
  })
}

export interface ApiErrorBody {
  code: string
  details?: Details
}

/**
 * Render an API error.
 *
 * Falls back through the code's dot-separated prefixes, so an unrecognised
 * `error.driver.something_new` still renders as the generic driver message
 * rather than as a raw code. A never-before-seen error is a bad message, not a
 * broken screen.
 */
export function translateError(locale: Locale, body: ApiErrorBody): string {
  const catalogue = catalogues[locale] ?? catalogues[DEFAULT_LOCALE]
  const parts = body.code.split('.')

  for (let end = parts.length; end > 0; end -= 1) {
    const candidate = parts.slice(0, end).join('.')
    if (candidate in catalogue) {
      return translate(locale, candidate as MessageKey, body.details)
    }
  }
  return translate(locale, 'error.internal')
}

export function createTranslator(locale: Locale) {
  return {
    locale,
    t: (key: MessageKey, details?: Details) => translate(locale, key, details),
    tError: (body: ApiErrorBody) => translateError(locale, body),
  }
}

export type Translator = ReturnType<typeof createTranslator>
