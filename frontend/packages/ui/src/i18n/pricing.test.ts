import { describe, expect, it } from 'vitest'

import { en, ru } from './messages'
import { createTranslator } from './translate'

/**
 * Every line code the pricing engine can emit.
 *
 * Kept in step with `backend/printorian/contexts/pricing/codes.py` — the backend's
 * `ALL_FIXED_CODES` tuple exists to be mirrored here. A code with no label renders
 * as a blank row in the customer's price breakdown, which quietly destroys the
 * transparency the whole feature is for.
 */
const ENGINE_LINE_CODES = [
  'material.filament',
  'material.purge',
  'machine.electricity',
  'machine.depreciation',
  'labor.supervision',
  'labor.setup',
  'labor.engineering',
  'logistics.packaging',
  'logistics.shipping',
  'overhead.general',
  'risk.failure_buffer',
  'adjustment.rush',
  'adjustment.volume_discount',
  'adjustment.customer_discount',
  'margin.profit',
] as const

/** Finish codes from the API's FINISH_CATALOGUE, which become `postprocess.<code>`. */
const FINISH_CODES = ['raw', 'sanded', 'primed', 'painted'] as const

const CATEGORIES = [
  'material',
  'machine',
  'labor',
  'logistics',
  'overhead',
  'risk',
  'adjustment',
  'margin',
] as const

describe('price breakdown labels', () => {
  it.each(ENGINE_LINE_CODES)('%s has a Russian and an English label', (code) => {
    expect(ru[code], `missing RU label for ${code}`).toBeTruthy()
    expect(en[code], `missing EN label for ${code}`).toBeTruthy()
  })

  it.each(FINISH_CODES)('postprocess.%s has both labels', (finish) => {
    const code = `postprocess.${finish}` as keyof typeof ru
    expect(ru[code]).toBeTruthy()
    expect(en[code]).toBeTruthy()
  })

  it.each(CATEGORIES)('category.%s has both labels', (category) => {
    const code = `category.${category}` as keyof typeof ru
    expect(ru[code]).toBeTruthy()
    expect(en[code]).toBeTruthy()
  })

  it('renders a line label in the active locale', () => {
    expect(createTranslator('ru').t('margin.profit')).toBe('Прибыль')
    expect(createTranslator('en').t('margin.profit')).toBe('Profit')
  })

  it('interpolates mesh warning details', () => {
    const rendered = createTranslator('en').t('warning.catalog.thin_walls', {
      approx_thickness_mm: '0.62',
    })
    expect(rendered).toBe('Thin walls: 0.62 mm')
  })

  it('has no label that is merely the code echoed back', () => {
    // A placeholder like `'margin.profit': 'margin.profit'` would pass a truthiness
    // check while telling the customer nothing.
    for (const code of ENGINE_LINE_CODES) {
      expect(ru[code]).not.toBe(code)
      expect(en[code]).not.toBe(code)
    }
  })

  it('distinguishes credits from charges by label, not just by sign', () => {
    // Discounts must read as discounts even before the number is seen.
    expect(ru['adjustment.volume_discount']).toContain('Скидка')
    expect(en['adjustment.volume_discount'].toLowerCase()).toContain('discount')
  })
})
