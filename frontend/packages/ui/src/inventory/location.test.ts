import { describe, expect, it } from 'vitest'

import { amsSlotLabel, formatLocation, summarizeLocations } from './location'

const NAMES = { 'printer-1': 'x2d-01' }

describe('amsSlotLabel', () => {
  it('labels a slot the way the machine does', () => {
    // Unit 0, index 2 is the slot an operator reads as A3 on the AMS itself.
    // Rendering the raw index would send someone to the wrong spool.
    expect(amsSlotLabel(0, 2)).toBe('A3')
    expect(amsSlotLabel(0, 0)).toBe('A1')
    expect(amsSlotLabel(1, 3)).toBe('B4')
  })

  it('is empty when the coordinates are missing', () => {
    expect(amsSlotLabel(null, null)).toBe('')
    expect(amsSlotLabel(0, undefined)).toBe('')
  })
})

describe('formatLocation', () => {
  it('names the shelf when there is one', () => {
    expect(formatLocation({ location_kind: 'stock', shelf: 'A1' }, 'en')).toBe('Shelf A1')
    expect(formatLocation({ location_kind: 'stock', shelf: 'A1' }, 'ru')).toBe('Полка A1')
  })

  it('falls back to plain stock when no shelf is recorded', () => {
    expect(formatLocation({ location_kind: 'stock', shelf: null }, 'en')).toBe('In stock')
  })

  it('names the printer and the slot', () => {
    expect(
      formatLocation(
        { location_kind: 'printer', printer_id: 'printer-1', ams_unit: 0, ams_slot: 2 },
        'en',
        NAMES,
      ),
    ).toBe('x2d-01 · A3')
  })

  it('degrades to the generic phrase for a printer it cannot name', () => {
    // The materials table is readable more widely than the fleet, so an
    // unresolved id must not surface as a UUID.
    const rendered = formatLocation(
      { location_kind: 'printer', printer_id: 'unknown-id', ams_unit: 0, ams_slot: 1 },
      'en',
      NAMES,
    )
    expect(rendered).toBe('In printer')
    expect(rendered).not.toContain('unknown-id')
  })

  it('handles the dryer and used-up spools', () => {
    expect(formatLocation({ location_kind: 'dryer' }, 'en')).toBe('Dryer')
    expect(formatLocation({ location_kind: 'consumed' }, 'en')).toBe('Consumed')
  })
})

describe('summarizeLocations', () => {
  it('lists each distinct place once', () => {
    const summary = summarizeLocations(
      [
        { location_kind: 'stock', shelf: 'A1' },
        { location_kind: 'stock', shelf: 'A1' },
        { location_kind: 'printer', printer_id: 'printer-1', ams_unit: 0, ams_slot: 0 },
      ],
      'en',
      NAMES,
    )
    expect(summary).toBe('Shelf A1 · x2d-01 · A1')
  })

  it('omits spools that have been used up', () => {
    // A consumed lot is not anywhere any more.
    expect(
      summarizeLocations(
        [
          { location_kind: 'consumed' },
          { location_kind: 'stock', shelf: 'B2' },
        ],
        'en',
      ),
    ).toBe('Shelf B2')
  })

  it('is empty for a material with no lots', () => {
    expect(summarizeLocations([], 'en')).toBe('')
  })
})
