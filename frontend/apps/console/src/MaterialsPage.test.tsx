/**
 * The wiring, which is the half of issue #38 no unit test of the window can see.
 *
 * `MaterialDetail.test.tsx` mounts the window directly and proves it reads
 * `GET /materials/{code}`. That leaves the question the issue actually asked —
 * whether anything on a screen *opens* it — resting on the docs gate, and that
 * gate is a source-text scan: a `MaterialDetail.tsx` no page mounted would still
 * carry the path literal and still make it green. `frontend/CLAUDE.md` says it in
 * one line: reachability is a question about the bundle, not the source.
 *
 * So this opens a row on the table a person actually opens it from, and watches
 * the request go out.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    calls: [] as string[],
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    state.calls.push(String(input))
    return state.handler(String(input), init)
  }) as unknown as typeof fetch
  return state
})

import type * as UiModule from '@printorian/ui'

import { MaterialsPage } from './MaterialsPage'

const session = vi.hoisted(() => ({ permissions: ['manage_inventory'] as string[] }))

vi.mock('@printorian/ui', async () => {
  const actual = await vi.importActual<typeof UiModule>('@printorian/ui')
  return {
    ...actual,
    useSession: () => ({
      ready: true,
      actor: { user_id: 'u1', permissions: session.permissions },
    }),
  }
})

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

/**
 * The row as `GET /materials` serves it *into this screen* — the eleven fields
 * `MaterialsPage` declares, and deliberately not the seven the window adds. A
 * fixture carrying the detail fields here would let a popup that read its row
 * pass this test, which is the mistake the issue was about.
 */
const row = {
  id: 'm1',
  code: 'PETG-CF-BLK',
  name: 'PETG Carbon Black',
  family: 'PETG',
  color_name: 'Чёрный',
  color_hex: '#111111',
  sell_price_per_gram: '3.74',
  status: 'stock',
  total_remaining_grams: '4200',
  lot_count: 1,
  lots: [
    {
      id: 'lot-4471',
      label: 'LOT.4471',
      remaining_grams: '4200',
      location_kind: 'stock',
      shelf: '5',
      printer_id: null,
      ams_unit: null,
      ams_slot: null,
    },
  ],
}

/** The same material as the detail route serves it: the row, plus the seven. */
const spec = {
  ...row,
  density_g_per_cm3: '1.27',
  purchase_price_per_1000m: '2940.00',
  tensile_mpa: '52',
  hdt_c: '78',
  is_flexible: false,
  is_outdoor_safe: true,
}

beforeEach(() => {
  net.calls = []
  session.permissions = ['manage_inventory']
  net.handler = (url: string) => {
    if (url.endsWith('/materials')) {
      return Promise.resolve(jsonOk({ rows: [row], counts: [{ status: 'stock', count: 1 }] }))
    }
    if (url.endsWith('/materials/PETG-CF-BLK')) return Promise.resolve(jsonOk(spec))
    return Promise.reject(new Error('unexpected request: ' + url))
  }
})

describe('the materials table', () => {
  it('opens a row into the detail route, not into the row it already has', async () => {
    render(<MaterialsPage locale="ru" />)

    const cell = await screen.findByText('PETG Carbon Black')
    const opened = cell.closest('tr')
    expect(opened).not.toBeNull()

    // Before the click, the table has been read and the material has not.
    expect(net.calls.some((url) => url.endsWith('/materials/PETG-CF-BLK'))).toBe(false)

    await userEvent.click(opened!)

    await waitFor(() =>
      expect(net.calls.some((url) => url.endsWith('/materials/PETG-CF-BLK'))).toBe(true),
    )

    // And the response reaches the screen: the density is in the detail payload
    // and in no column of the table above it, so a window rendered from the row
    // could not have drawn it.
    expect(await screen.findByText('1.27')).toBeInTheDocument()
  })
})
