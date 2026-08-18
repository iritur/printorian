/**
 * The shapes a report's body is built from.
 *
 * Mirrors `contexts/journal/schemas.py`. Structured blocks rather than markup, for
 * the reason stated there: the kit's article is prose *punctuated* by specific
 * components — a rule callout, a pull quote with its citation, a code listing with
 * a header bar, a figures panel reusing the pricing screen's leader rows — and
 * none of those survive a round trip through markdown.
 *
 * The second reason is why the renderer never touches `dangerouslySetInnerHTML`:
 * nothing here can carry HTML, so a published report cannot put a script on the
 * storefront even if whoever holds `MANAGE_JOURNAL` wanted it to.
 */

/** A figures row's tone. Named for its block, because `Tone` is already the
 *  data table's — two different vocabularies that would otherwise collide. */
export type FigureTone = 'plain' | 'good' | 'warn' | 'bad'

export interface Heading {
  kind: 'heading'
  text: string
}

export interface Paragraph {
  kind: 'paragraph'
  /** Carries `**bold**` and `` `code` `` and nothing else. See `renderInline`. */
  text: string
}

export interface ListBlock {
  kind: 'list'
  items: string[]
}

export interface Callout {
  kind: 'callout'
  title: string
  text: string
  tone: 'plain' | 'live'
}

export interface Quote {
  kind: 'quote'
  text: string
  cite: string
}

export interface Code {
  kind: 'code'
  label: string
  note: string
  code: string
}

export interface TableBlock {
  kind: 'table'
  head: string[]
  rows: string[][]
  align: ('start' | 'end')[]
}

export interface FigureRow {
  label: string
  value: string
  tone: FigureTone
}

export interface Figures {
  kind: 'figures'
  title: string
  aside: string
  rows: FigureRow[]
  total_label: string
  total_value: string
  note: string
}

export type Block =
  | Heading
  | Paragraph
  | ListBlock
  | Callout
  | Quote
  | Code
  | TableBlock
  | Figures

export const BLOCK_KINDS = [
  'heading',
  'paragraph',
  'list',
  'callout',
  'quote',
  'code',
  'table',
  'figures',
] as const

/** The kit's five sections, in the order its filter row lists them. */
export const SECTIONS = ['cost', 'materials', 'fleet', 'architecture', 'postprocessing'] as const

export type Section = (typeof SECTIONS)[number]

/**
 * Section labels and the tone their filter chip carries.
 *
 * The tones are the kit's, and they are not decorative: `live` marks the section
 * the farm's own argument turns on, `good` and `warn` follow the same vocabulary
 * the machine states use, so a reader who has seen the fleet screen already knows
 * how to read them.
 */
export const SECTION_META: Record<Section, { label: string; tone?: 'live' | 'good' | 'warn' }> = {
  cost: { label: 'Себестоимость', tone: 'live' },
  materials: { label: 'Материалы', tone: 'good' },
  fleet: { label: 'Парк', tone: 'warn' },
  architecture: { label: 'Архитектура' },
  postprocessing: { label: 'Постобработка' },
}

/**
 * A URL for a Russian title.
 *
 * The same rule as `policies.py::slugify`, and it has to stay the same: the server
 * derives a heading's anchor from its text and the client derives the `id` it
 * scrolls to. Two spellings and the contents list stops working.
 */
export function slugify(title: string): string {
  const lowered = title.trim().toLowerCase()
  let latin = ''
  for (const char of lowered) latin += TRANSLITERATION[char] ?? char
  return latin
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120)
}

/** The `id` a heading carries. Position-suffixed, so repeated titles stay distinct. */
export function anchorOf(text: string, index: number): string {
  return `${slugify(text) || 'section'}-${index + 1}`
}

const TRANSLITERATION: Record<string, string> = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e',
  ж: 'zh', з: 'z', и: 'i', й: 'i', к: 'k', л: 'l', м: 'm',
  н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u',
  ф: 'f', х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'shch',
  ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya',
}
