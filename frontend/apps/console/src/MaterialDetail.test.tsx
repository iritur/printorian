/**
 * The materials detail window, on the things the table it opens from cannot say.
 *
 * `GET /materials/{code}` was served and called by nothing (DESIGN-KIT §4, issue
 * #38). What is asserted here is that it is called, that the fields only it
 * carries reach the screen, and that the two ways of getting this wrong are not
 * taken: a figure nobody recorded printed as a number, and a code the farm does
 * not know answered with a window full of blanks.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    calls: [] as { url: string; method: string }[],
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    state.calls.push({ url: String(input), method: init?.method ?? 'GET' })
    return state.handler(String(input), init)
  }) as unknown as typeof fetch
  return state
})

import type * as UiModule from '@printorian/ui'

import { MaterialDetail } from './MaterialDetail'

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

function notFound(): Response {
  return {
    ok: false,
    status: 404,
    json: () =>
      Promise.resolve({ code: 'error.inventory.spec_not_found', details: { material_code: 'NOPE' } }),
  } as unknown as Response
}

/** `MaterialSpecView` as the route serves it — every field, measured. */
function aSpec(overrides: Record<string, unknown> = {}) {
  return {
    id: 'm1',
    code: 'PETG-CF-BLK',
    name: 'PETG Carbon Black',
    family: 'PETG',
    color_name: 'Чёрный',
    color_hex: '#111111',
    density_g_per_cm3: '1.27',
    sell_price_per_gram: '3.74',
    purchase_price_per_1000m: '2940.00',
    tensile_mpa: '52',
    hdt_c: '78',
    is_flexible: false,
    is_outdoor_safe: true,
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
    ...overrides,
  }
}

function open(props: Partial<Parameters<typeof MaterialDetail>[0]> = {}) {
  return render(
    <MaterialDetail
      code="PETG-CF-BLK"
      locale="ru"
      printerNames={{}}
      onClose={() => {}}
      onChanged={() => {}}
      {...props}
    />,
  )
}

beforeEach(() => {
  net.calls = []
  session.permissions = ['manage_inventory']
  net.handler = (url: string) => {
    if (url.endsWith('/materials/PETG-CF-BLK')) return Promise.resolve(jsonOk(aSpec()))
    return Promise.reject(new Error('unexpected request: ' + url))
  }
})

describe('the material detail window', () => {
  it('reads the material by code, and shows what only that response carries', async () => {
    open()

    // The point of the issue: the window asks the detail route rather than
    // rendering the row it was opened from.
    await waitFor(() =>
      expect(net.calls.some((call) => call.url.endsWith('/materials/PETG-CF-BLK'))).toBe(true),
    )

    // None of these three are in a column of the materials table.
    expect(await screen.findByText('1.27')).toBeInTheDocument()
    expect(screen.getByText('52')).toBeInTheDocument()
    expect(screen.getByText('78')).toBeInTheDocument()
  })

  it('shows an em dash for a property nobody recorded, never a zero', async () => {
    // ADR-0007 in its most ordinary costume: `Number(null)` is 0, and a PETG with
    // no recorded tensile figure would read as one that snaps under no load.
    net.handler = () => Promise.resolve(jsonOk(aSpec({ tensile_mpa: null, hdt_c: null })))
    open()

    const tensile = (await screen.findByText('Прочность на разрыв, МПа')).closest('li')
    const hdt = screen.getByText('Температура размягчения, °C').closest('li')

    expect(tensile?.textContent).toContain('—')
    expect(tensile?.textContent).not.toMatch(/\d/)
    expect(hdt?.textContent).toContain('—')
    expect(hdt?.textContent).not.toMatch(/\d/)
  })

  it('says a code the farm does not know is unknown, and draws no figures', async () => {
    net.handler = () => Promise.resolve(notFound())
    open({ code: 'NOPE' })

    expect(await screen.findByRole('alert')).toHaveTextContent('Материал не найден')
    // An empty grid under an error banner reads as "this material has nothing",
    // which is a measurement the farm never took.
    expect(screen.queryByText('Свойства')).not.toBeInTheDocument()
    expect(screen.queryByText('Партии')).not.toBeInTheDocument()
  })

  it('keeps the purchase price behind VIEW_FINANCIALS', async () => {
    // The route serves the field to anyone who may read materials; the console
    // does not put the farm's buying price in front of a shop-floor engineer.
    open()
    expect(await screen.findByText('Свойства')).toBeInTheDocument()
    expect(screen.queryByText('Закупка за 1000 м')).not.toBeInTheDocument()
  })

  it('shows the purchase price to someone who may read money', async () => {
    session.permissions = ['manage_inventory', 'view_financials']
    open()

    expect(await screen.findByText('Закупка за 1000 м')).toBeInTheDocument()
  })

  it('re-reads the material after a lot is added, instead of closing', async () => {
    // The window used to render a captured row, so a new lot could only be seen
    // by closing and reopening. Adding one now refreshes the window it happened
    // in — and tells the table behind it to reload as well.
    let reads = 0
    const changed = vi.fn()
    net.handler = (url: string, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'POST' && url.endsWith('/materials/lots')) {
        return Promise.resolve(jsonOk({ id: 'lot-4472' }))
      }
      reads += 1
      return Promise.resolve(
        jsonOk(
          reads === 1
            ? aSpec()
            : aSpec({
                lot_count: 2,
                lots: [
                  ...aSpec().lots,
                  {
                    id: 'lot-4472',
                    label: 'LOT.4472',
                    remaining_grams: '1000',
                    location_kind: 'stock',
                    shelf: null,
                    printer_id: null,
                    ams_unit: null,
                    ams_slot: null,
                  },
                ],
              }),
        ),
      )
    }

    open({ onChanged: changed })

    await userEvent.click(await screen.findByRole('button', { name: 'Добавить партию' }))

    expect(await screen.findByText('LOT.4472')).toBeInTheDocument()
    expect(reads).toBe(2)
    expect(changed).toHaveBeenCalled()
  })
})
