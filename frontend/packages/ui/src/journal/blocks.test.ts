import { describe, expect, it } from 'vitest'

import { anchorOf, slugify } from './blocks'

/*
 * `slugify` has to agree with `policies.py::slugify` character for character: the
 * server derives a heading's anchor from its text and the client derives the `id`
 * it scrolls to. Every expected value below was produced by the Python function,
 * so a drift on either side fails here rather than as a contents list that
 * silently stops scrolling.
 *
 * The edge cases are the ones the trimming step depends on. It removes one hyphen
 * at each end, which is only right because the step before it collapses every run
 * of non-alphanumerics, hyphens included, into one — so the runs are what is
 * tested, at the ends and in the middle.
 */
describe('slugify', () => {
  it.each([
    ['Час печати', 'chas-pechati'],
    ['ёлка и щётка', 'elka-i-shchetka'],
    ['  — Итоги, 2026! ', 'itogi-2026'],
    ['PLA --- PETG', 'pla-petg'],
    ['---Сушка---', 'sushka'],
    ['!!!', ''],
  ])('%j becomes %j, as the server spells it', (title, expected) => {
    expect(slugify(title)).toBe(expected)
  })

  it('collapses a long run of hyphens to one rather than trimming it away', () => {
    expect(slugify(`a${'-'.repeat(5000)}b`)).toBe('a-b')
  })
})

describe('anchorOf', () => {
  it('suffixes the position, so two headings with one title stay distinct', () => {
    expect(anchorOf('Час печати', 2)).toBe('chas-pechati-3')
  })

  it('falls back to a word when the title transliterates to nothing', () => {
    expect(anchorOf('!!!', 0)).toBe('section-1')
  })
})
