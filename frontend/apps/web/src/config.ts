/** Shapes shared by the configurator. */

export const MAX_COLORS = 4

/**
 * Finish codes the API offers (api/routers/pricing.py FINISH_CATALOGUE).
 *
 * Mutually exclusive, not additive: they are increasing levels of finishing, so
 * "as printed" and "painted" together would be a contradiction. The first entry is
 * the default, and there is always exactly one selected.
 */
export const FINISHES = ['raw', 'sanded', 'primed', 'painted'] as const

export const DEFAULT_FINISH = FINISHES[0]

/** Statuses that mean the farm already holds the filament. */
export const IN_STOCK_STATUSES = ['stock', 'in_printer'] as const

/**
 * What the customer chose.
 *
 * Colour and material are **independent axes**, which is how a customer thinks
 * about it: "three colours, in PETG". Whether the shop happens to hold that exact
 * spool is the farm's problem to solve, and reaches the customer only as a line
 * in the price — never as a choice they are steered away from.
 */
export interface Config {
  /** Material family: PLA, PETG, ABS… One per plate. */
  material: string
  /** Colour name per slot. Its length is the colour count. */
  colors: string[]
  quantity: number
  /** Decimal string, never a float — money and geometry stay exact. */
  scale: string
  finishes: string[]
  rush: boolean
}

export interface Material {
  code: string
  name: string
  family: string
  color_name: string
  color_hex: string
  status: string
  sell_price_per_gram: string
}

export const isInStock = (material: Material): boolean =>
  (IN_STOCK_STATUSES as readonly string[]).includes(material.status)

/** Material families the shop sells, in catalogue order. */
export function families(catalogue: Material[]): string[] {
  return [...new Set(catalogue.map((material) => material.family))]
}

/**
 * Colours offered in one material.
 *
 * Per family rather than a global list: PETG comes in Clear and the others do
 * not, and offering a colour that cannot be bought in the chosen material would
 * be a choice that quietly fails later.
 */
export function coloursFor(family: string, catalogue: Material[]): Material[] {
  const seen = new Set<string>()
  return catalogue
    .filter((material) => material.family === family)
    .filter((material) => (seen.has(material.color_name) ? false : seen.add(material.color_name)))
}

/** The product for one (material, colour) pair, if the shop sells it. */
export function specFor(
  family: string,
  colour: string,
  catalogue: Material[],
): Material | undefined {
  return catalogue.find(
    (material) => material.family === family && material.color_name === colour,
  )
}

/** Every product the current configuration needs, one per slot. */
export function chosenSpecs(config: Config, catalogue: Material[]): Material[] {
  return config.colors
    .map((colour) => specFor(config.material, colour, catalogue))
    .filter((material): material is Material => Boolean(material))
}

/** Chosen products the farm does not hold, and so must buy in. */
export function needingProcurement(config: Config, catalogue: Material[]): Material[] {
  const seen = new Set<string>()
  return chosenSpecs(config, catalogue)
    .filter((material) => !isInStock(material))
    .filter((material) => (seen.has(material.code) ? false : seen.add(material.code)))
}

/**
 * The product a quote is priced from.
 *
 * The engine prices one material per plate, and colours of the same family can
 * differ in price. The dearest is used so a quote never lands *under* what the
 * plate costs; quoting the cheapest would mean absorbing the difference on every
 * multi-colour order.
 */
export function pricingSpec(config: Config, catalogue: Material[]): Material | undefined {
  return chosenSpecs(config, catalogue).sort(
    (a, b) => Number(b.sell_price_per_gram) - Number(a.sell_price_per_gram),
  )[0]
}

/**
 * Keep the colour list at `count` entries.
 *
 * New slots take the next colour the customer has *not* already chosen. Asking
 * for three colours and being given the same one three times is not what anyone
 * means by it — and it would price as a single-colour plate, so the number the
 * customer picked would not match the number they were charged for.
 *
 * Shrinking drops from the end, keeping the choices made first.
 */
export function resizeColours(current: string[], count: number, palette: Material[]): string[] {
  if (count <= current.length) return current.slice(0, count)

  const chosen = [...current]
  for (const option of palette) {
    if (chosen.length >= count) break
    if (!chosen.includes(option.color_name)) chosen.push(option.color_name)
  }
  // A palette smaller than the requested count cannot fill every slot; the last
  // colour repeats rather than leaving a slot empty, which nothing could print.
  while (chosen.length < count && chosen.length > 0) {
    chosen.push(chosen[chosen.length - 1] as string)
  }
  return chosen
}
