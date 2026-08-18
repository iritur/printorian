import { translate } from '../i18n/translate'
import type { Locale } from '../i18n/messages'

/**
 * Where a physical lot is, rendered from its parts.
 *
 * The backend sends `location_kind` plus the coordinates and never a sentence
 * (ADR-0012), so assembling "in printer via-ui-01 · A3" is the client's job.
 * It lives here rather than in a screen because the materials table, the lot
 * detail and later the Electron console all have to say the same thing the same
 * way — the scenario asks for this in more than one place.
 */

export interface LotLocation {
  location_kind: string
  shelf?: string | null
  printer_id?: string | null
  ams_unit?: number | null
  ams_slot?: number | null
}

/**
 * AMS slots are labelled by unit letter and 1-based position on the machine
 * itself — unit 0, index 2 is the slot an operator reads as "A3". Showing the
 * raw zero-based index would send someone to the wrong spool.
 */
export function amsSlotLabel(unit: number | null | undefined, slot: number | null | undefined) {
  if (unit === null || unit === undefined || slot === null || slot === undefined) return ''
  return `${String.fromCharCode(65 + unit)}${slot + 1}`
}

/**
 * `printerNames` maps printer id to display name. A lot can sit in a printer the
 * caller cannot see (the materials table is readable more widely than the
 * fleet), so an unresolved id degrades to the generic "in printer" rather than
 * printing a UUID at someone.
 */
export function formatLocation(
  location: LotLocation,
  locale: Locale,
  printerNames: Record<string, string> = {},
): string {
  switch (location.location_kind) {
    case 'printer': {
      const slot = amsSlotLabel(location.ams_unit, location.ams_slot)
      const name = location.printer_id ? printerNames[location.printer_id] : undefined
      if (!name) return translate(locale, 'location.printer')
      return slot
        ? translate(locale, 'location.printer.slot', { printer: name, slot })
        : `${translate(locale, 'location.printer')} · ${name}`
    }
    case 'dryer':
      return translate(locale, 'location.dryer')
    case 'consumed':
      return translate(locale, 'location.consumed')
    default:
      return location.shelf
        ? translate(locale, 'location.shelf', { shelf: location.shelf })
        : translate(locale, 'location.stock')
  }
}

/**
 * One line for a material whose lots are spread across places.
 *
 * Distinct places only, in the order first seen: a material with four spools on
 * the same shelf reads "Shelf A1", not the same phrase four times.
 */
export function summarizeLocations(
  lots: readonly LotLocation[],
  locale: Locale,
  printerNames: Record<string, string> = {},
): string {
  const seen: string[] = []
  for (const lot of lots) {
    // A spool that has been used up is not anywhere any more; listing it would
    // pad the summary with places nothing is actually stored.
    if (lot.location_kind === 'consumed') continue
    const label = formatLocation(lot, locale, printerNames)
    if (!seen.includes(label)) seen.push(label)
  }
  return seen.join(' · ')
}
