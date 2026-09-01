/**
 * The journal's chip row, held to ADR-0007 at the screen rather than at the
 * component.
 *
 * `FilterChips.test.tsx` proves the component prints «—» when it is handed
 * `count: null`. It cannot prove this page hands it one — and that is the half
 * that was wrong: the section chips printed `0` for counts the server had not
 * sent while the «Все» chip beside them printed «—», one row of numbers making
 * two different promises about the same missing answer.
 *
 * The proof that the gap was real: the fix at `JournalPage.tsx`'s `counted` memo
 * was reverted to its pre-#88 form and the whole suite stayed green. So both
 * cases are asserted here, because the failure is not "shows «—»" — it is the
 * two facts collapsing into one. A section nobody has counted yet and a section
 * counted at zero must not render the same.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

/**
 * Installed before the imports run.
 *
 * `ApiClient` binds `globalThis.fetch` in its constructor and `@printorian/ui`
 * builds one at module scope, so a stub installed in `beforeEach` would arrive
 * after the real thing had already been captured. The wrapper stays for the
 * file's lifetime and delegates to a handler each test replaces.
 */
const net = vi.hoisted(() => {
  const state = {
    handler: (() => Promise.reject(new Error('no handler'))) as (url: string) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL) =>
    state.handler(String(input))) as unknown as typeof fetch
  return state
})

import { JournalPage } from './JournalPage'

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

/**
 * A request that has been made and not answered.
 *
 * Not a rejection: `JournalPage`'s catch installs a whole zeroed index, so a
 * failed request would be testing a different branch entirely — see the note at
 * the bottom of this file. This is the state the page is in for the length of
 * every load, before anything has gone wrong.
 */
const pending = () => new Promise<never>(() => {})

describe('before the index arrives', () => {
  it('says it has not counted the sections, rather than counting them at zero', () => {
    net.handler = pending
    render(<JournalPage locale="ru" onRead={vi.fn()} />)

    // All five, because the defect was per-chip: «Все» read «—» correctly while
    // every section beside it claimed a measurement nobody had taken.
    for (const label of [
      'Все',
      'Себестоимость',
      'Материалы',
      'Парк',
      'Архитектура',
      'Постобработка',
    ]) {
      expect(
        screen.getByRole('button', { name: new RegExp(`${label}\\s*—`) }),
      ).toBeTruthy()
    }

    // And nothing anywhere in the row is a digit yet. Read off the group's own
    // text rather than the screen's, so a figure elsewhere on the page cannot
    // fail it and a chip that started printing `1` cannot slip past it.
    const chips = screen.getByRole('group', { name: 'Разделы журнала' })
    expect(chips.textContent).not.toMatch(/\d/)
  })
})

describe('once it has', () => {
  /**
   * A journal the server has counted and found empty.
   *
   * `counts: []` is the shape that matters: the server sends a row only for a
   * section that has something in it, so "counted at zero" arrives as an absent
   * entry — indistinguishable from "not counted yet" unless the page keeps the
   * two apart before the answer lands rather than after.
   */
  it('renders a measured zero as zero, because "none" is an answer', async () => {
    net.handler = (url: string) => {
      if (url.includes('/journal/latest')) return Promise.resolve(jsonOk(null))
      return Promise.resolve(
        jsonOk({ rows: [], counts: [], total: 0, published_total: 0, weekly_rate: null }),
      )
    }
    render(<JournalPage locale="ru" onRead={vi.fn()} />)

    expect(
      await screen.findByRole('button', { name: /Себестоимость\s*0/ }),
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: /Все\s*0/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Постобработка\s*0/ })).toBeTruthy()
  })

  it('carries each section its own count through to its own chip', async () => {
    net.handler = (url: string) => {
      if (url.includes('/journal/latest')) return Promise.resolve(jsonOk(null))
      return Promise.resolve(
        jsonOk({
          rows: [],
          counts: [
            { section: 'cost', count: 5 },
            { section: 'fleet', count: 2 },
          ],
          total: 7,
          published_total: 7,
          weekly_rate: null,
        }),
      )
    }
    render(<JournalPage locale="ru" onRead={vi.fn()} />)

    expect(await screen.findByRole('button', { name: /Себестоимость\s*5/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Парк\s*2/ })).toBeTruthy()
    // Present in the answer's shape, absent from its rows: the server counted
    // and found none, which is a fact and prints as one.
    expect(screen.getByRole('button', { name: /Материалы\s*0/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Все\s*7/ })).toBeTruthy()
  })
})

/*
  NOT COVERED HERE, AND KNOWN.

  `JournalPage.tsx`'s `.catch(() => setIndex({ rows: [], counts: [], total: 0,
  published_total: 0, weekly_rate: null }))` turns a failed request into a
  measured empty journal: a backend outage renders «Все 0» and «0 ВЫПУСКОВ»,
  which is the same invented measurement one branch above the one this file
  pins. It predates #88 and is deliberately left alone here rather than fixed in
  a follow-up that is about the loading state — pinning it now would be pinning
  the wrong behaviour. It is written down in HANDOFF rather than fixed silently.
*/
