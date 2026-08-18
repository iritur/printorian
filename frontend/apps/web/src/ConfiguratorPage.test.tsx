/**
 * The hover preview must describe what the pointer is on — and nothing else.
 *
 * This has regressed three times by hand, each time in the same shape: the panel
 * kept describing an option the customer had already moved away from. The reset
 * boundary is subtle enough that it is worth pinning rather than re-checking in a
 * browser — too tight and the preview flickers while crossing a row of buttons,
 * too loose and it strands on screen.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The fetch stub is installed *before* the imports run.
 *
 * `ApiClient` binds `globalThis.fetch` in its constructor, and the page
 * constructs one at module scope — so a stub installed in `beforeEach` arrives
 * after the client has already captured the real thing. The wrapper below stays
 * constant for the file's lifetime and delegates to a handler each test can
 * replace, which is what makes it swappable *and* early enough.
 */
const net = vi.hoisted(() => {
  const state = {
    handler: (() => Promise.reject(new Error('no handler'))) as (url: string) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL) =>
    state.handler(String(input))) as unknown as typeof fetch
  return state
})

import { ConfiguratorPage } from './ConfiguratorPage'

const MATERIALS = [
  {
    code: 'pla-white',
    name: 'PLA White',
    family: 'PLA',
    color_name: 'White',
    color_hex: '#F5F5F5',
    status: 'stock',
    sell_price_per_gram: '2.20',
  },
  {
    code: 'pla-red',
    name: 'PLA Red',
    family: 'PLA',
    color_name: 'Red',
    color_hex: '#C0392B',
    status: 'stock',
    sell_price_per_gram: '2.40',
  },
  {
    code: 'pla-blue',
    name: 'PLA Blue',
    family: 'PLA',
    color_name: 'Blue',
    color_hex: '#2F6FED',
    status: 'ordered',
    sell_price_per_gram: '2.40',
  },
  // Last, so `families()[0]` is still PLA and the default-family tests are
  // untouched. Its palette only appears once PETG is the chosen family.
  {
    code: 'petg-black',
    name: 'PETG Black',
    family: 'PETG',
    color_name: 'Black',
    color_hex: '#1B1B1E',
    status: 'stock',
    sell_price_per_gram: '2.90',
  },
]

/**
 * What `GET /materials/recommend` answers with: scored *specs*, so a family the
 * shop carries in two colours comes back twice. Deliberately shaped that way —
 * collapsing it to one row per family is the step's job, and a fixture with one
 * colour per family would never exercise it.
 */
const RECOMMEND = [
  {
    score: 3,
    reasons: ['match.tensile', 'match.in_stock'],
    spec: {
      code: 'pla-white',
      name: 'PLA White',
      family: 'PLA',
      density_g_per_cm3: '1.24',
      sell_price_per_gram: '2.20',
      tensile_mpa: '50',
      hdt_c: '60',
      is_flexible: false,
      is_outdoor_safe: false,
      status: 'stock',
      total_remaining_grams: '1200',
    },
  },
  {
    score: 2,
    reasons: ['match.tensile'],
    spec: {
      code: 'pla-red',
      name: 'PLA Red',
      family: 'PLA',
      density_g_per_cm3: '1.24',
      sell_price_per_gram: '2.40',
      tensile_mpa: '50',
      hdt_c: '60',
      is_flexible: false,
      is_outdoor_safe: false,
      status: 'stock',
      total_remaining_grams: '800',
    },
  },
  {
    score: 1,
    reasons: ['match.hdt'],
    spec: {
      code: 'petg-black',
      name: 'PETG Black',
      family: 'PETG',
      density_g_per_cm3: '1.27',
      sell_price_per_gram: '2.90',
      tensile_mpa: '52',
      hdt_c: '70',
      is_flexible: false,
      is_outdoor_safe: true,
      status: 'stock',
      total_remaining_grams: '500',
    },
  },
]

const MODEL = {
  model_filename: 'cube.stl',
  triangle_count: 12,
  volume_cm3: '64.0',
  bounding_box_mm: { x: '40.0', y: '40.0', z: '40.0' },
  estimated_minutes: 96,
  estimated_grams: '80.0',
  mesh_warnings: [],
}

const BREAKDOWN = {
  currency: 'RUB',
  quantity: 1,
  total: '800.00',
  unit_price: '800.00',
  by_category: {},
  lines: [
    {
      code: 'material.filament',
      category: 'material',
      amount: '800.00',
      basis: {
        kind: 'rate_over_quantity',
        quantity: '80',
        unit: 'gram',
        rate: '2.40',
        percent: null,
        of_codes: [],
        tier_min_quantity: null,
      },
    },
  ],
}

const DELTA = {
  currency: 'RUB',
  comparable: true,
  total_before: '800.00',
  total_after: '950.00',
  total_change: '150.00',
  changed: [
    {
      code: 'postprocess.painted',
      category: 'postprocess',
      before: '0.00',
      after: '150.00',
      change: '150.00',
      is_new: true,
      is_removed: false,
    },
  ],
}

/** A binary response, the way `GET /catalog/{slug}/model` answers. */
function meshOk(name: string): Response {
  return {
    ok: true,
    status: 200,
    headers: { get: (key: string) => (/content-disposition/i.test(key) ? `inline; filename="${name}"` : null) },
    blob: () => Promise.resolve(new Blob([new Uint8Array([0, 1, 2])], { type: 'model/stl' })),
  } as unknown as Response
}

function jsonOk(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as unknown as Response
}

/** Resolvers for in-flight previews, so a test can hold one open deliberately. */
let held: Array<() => void> = []
let holdPreviews = false

beforeEach(() => {
  held = []
  holdPreviews = false
  net.handler = (url: string) => {
    // Before the catalogue branch: `/materials/recommend` also contains
    // `/materials`, and answering it with `{rows}` hands the step something it
    // cannot iterate.
    if (/\/catalog\/[^/]+\/model/.test(url)) return Promise.resolve(meshOk('bracket_v4.stl'))
    if (url.includes('/materials/recommend')) return Promise.resolve(jsonOk(RECOMMEND))
    if (url.includes('/materials')) return Promise.resolve(jsonOk({ rows: MATERIALS }))
    if (url.includes('/pricing/quote')) {
      return Promise.resolve(jsonOk({ breakdown: BREAKDOWN, model: MODEL }))
    }
    if (url.includes('/pricing/preview')) {
      const answer = jsonOk({ delta: DELTA })
      if (!holdPreviews) return Promise.resolve(answer)
      return new Promise<Response>((resolve) => held.push(() => resolve(answer)))
    }
    return Promise.resolve(jsonOk({}))
  }
})

/** A configurator with a model attached and a quote on screen. */
async function configured(user: ReturnType<typeof userEvent.setup>) {
  render(<ConfiguratorPage locale="ru" onCheckout={() => undefined} />)
  // A real STL is unnecessary: the server parses it, and the server is stubbed.
  const model = new File([new Uint8Array([0, 1, 2])], 'cube.stl', { type: 'model/stl' })
  await user.upload(screen.getByLabelText('STL'), model)
  await screen.findByRole('region', { name: /цен/i })
  return model
}

const deltaShown = () => document.querySelector('.hv-delta') !== null

/**
 * Move the pointer from one element to another, the way a browser reports it.
 *
 * `user-event` cannot be used for this. jsdom has no layout, so every `hover()`
 * re-enters from the document root and fires `mouseleave` on *every* ancestor —
 * including the panel, whose own handler would then clear the preview and make
 * these tests pass no matter where the reset lives. A real browser emits
 * `mouseout`/`mouseover` carrying `relatedTarget`, and React derives enter and
 * leave from exactly that pair, so this is the faithful model: an ancestor
 * shared by `from` and `to` never sees a leave.
 */
function movePointer(from: Element, to: Element): void {
  fireEvent.mouseOut(from, { relatedTarget: to })
  fireEvent.mouseOver(to, { relatedTarget: from })
}

/** Put the pointer on an element, arriving from outside the page. */
function movePointerOnto(target: Element): void {
  fireEvent.mouseOver(target, { relatedTarget: document.body })
}

/**
 * Every group of options that previews on hover, by its accessible name.
 *
 * The kit's panels replaced the old numbered fieldsets, so these are the labels
 * the markup now carries: the colour-count segment, the palette, and the finish
 * panel — which is a panel rather than a labelled group, so it is matched by class.
 */
const GROUPS = ['Сколько цветов', 'Палитра в наличии'] as const

/** The «04 :: Обработка и срок» panel, whose buttons are `hv-option`. */
const finishPanel = () =>
  (document.querySelectorAll('.hv-panel')[3] as HTMLElement | undefined) ??
  (() => {
    throw new Error('finish panel not rendered')
  })()

/** The finish and rush buttons, in the order the kit lists them. */
const finishOptions = () =>
  [...document.querySelectorAll('.hv-option')] as HTMLElement[]

describe('hover preview', () => {
  it('describes the option under the pointer', async () => {
    const user = userEvent.setup()
    await configured(user)

    movePointerOnto(finishOptions()[3] as HTMLElement)

    await waitFor(() => expect(deltaShown()).toBe(true))
  })

  it.each(GROUPS)('resets when the pointer leaves the group (%s)', async (legend) => {
    const user = userEvent.setup()
    await configured(user)

    const group = screen.getByRole('group', { name: legend })
    const buttons = within(group).getAllByRole('button')
    movePointerOnto(buttons[buttons.length - 1] as HTMLElement)
    await waitFor(() => expect(deltaShown()).toBe(true))

    // The panel head is still *inside* the panel, so the group's own handler is
    // what fires here. That is the whole bug: resetting on the panel alone
    // stranded the table on screen for exactly this move.
    movePointer(buttons[buttons.length - 1] as HTMLElement, group.closest('.hv-panel')!)
    await waitFor(() => expect(deltaShown()).toBe(false))
  })

  it('survives a sweep between options in the same group', async () => {
    const user = userEvent.setup()
    await configured(user)

    const buttons = finishOptions()
    movePointerOnto(buttons[0] as HTMLElement)
    await waitFor(() => expect(deltaShown()).toBe(true))

    // Crossing a row on the way to a choice must not blank the panel between
    // each button — that flicker is what per-button leave handlers caused.
    const trail: boolean[] = []
    buttons.slice(1).forEach((button, index) => {
      movePointer(buttons[index] as HTMLElement, button)
      trail.push(deltaShown())
    })
    expect(trail).toEqual(trail.map(() => true))
  })

  it('drops an answer that arrives after the pointer has gone', async () => {
    const user = userEvent.setup()
    await configured(user)
    holdPreviews = true

    const button = finishOptions()[3] as HTMLElement
    movePointerOnto(button)
    await waitFor(() => expect(held.length).toBe(1))

    movePointer(button, finishPanel())
    held.forEach((resolve) => resolve())

    // A slow preview landing after the customer looked away would describe an
    // option nobody is pointing at, and would resurrect a table they saw close.
    await waitFor(() => expect(deltaShown()).toBe(false))
  })

  it('shows the change above the breakdown, so the panel does not grow at the bottom', async () => {

    const user = userEvent.setup()
    await configured(user)

    movePointerOnto(finishOptions()[3] as HTMLElement)
    await waitFor(() => expect(deltaShown()).toBe(true))

    const delta = document.querySelector('.hv-delta') as HTMLElement
    const breakdown = document.querySelector('.hv-price') as HTMLElement
    expect(delta.compareDocumentPosition(breakdown) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

/** The colour-count buttons, by their labels. */
const counts = () =>
  within(screen.getByRole('group', { name: 'Сколько цветов' })).getAllByRole(
    'button',
  ) as HTMLElement[]

/** The chosen count, read off whichever button is pressed. */
const chosenCount = () =>
  Number(counts().find((button) => button.getAttribute('aria-pressed') === 'true')?.textContent)

/**
 * The palette row of «02 :: Цвет».
 *
 * Scoped to the palette group rather than the panel, because a slot card is
 * labelled «Цвет 2 :: Red» — an unanchored search picks it up, and a click then
 * lands on the slot instead of the swatch, so the test quietly stops exercising
 * what it names.
 */
const swatches = () =>
  within(screen.getByRole('group', { name: 'Палитра в наличии' })).getAllByRole('button', {
    name: /^(White|Red|Blue)$/,
  }) as HTMLElement[]

/** The AMS slot cards, one per colour on the plate. */
const slots = () => [...document.querySelectorAll('.hv-slot')] as HTMLElement[]

/**
 * The colour each slot holds, read off the card's own heading.
 *
 * From the visible text rather than an attribute: the heading is exactly what the
 * customer sees, so a change that broke the label fails here too.
 */
const heldColours = () =>
  slots().map((slot) => slot.querySelector('.hv-h')?.textContent?.split(' :: ')[1])

/** One swatch, scoped to the palette, so a slot card can never be clicked instead. */
const swatchNamed = (name: string) =>
  within(screen.getByRole('group', { name: 'Палитра в наличии' })).getByRole('button', {
    name,
  }) as HTMLElement

describe('choosing colours', () => {
  it('leaves the count alone when a colour is picked', async () => {
    const user = userEvent.setup()
    await configured(user)

    await user.click(counts()[2] as HTMLElement)
    expect(chosenCount()).toBe(3)

    // Picking a colour used to add or remove an entry, which *is* the count —
    // so answering step 3 silently re-answered step 2.
    await user.click(swatches()[1] as HTMLElement)
    expect(chosenCount()).toBe(3)

    await user.click(swatches()[0] as HTMLElement)
    expect(chosenCount()).toBe(3)
  })

  it('offers exactly one slot card per colour on the plate', async () => {
    const user = userEvent.setup()
    await configured(user)

    // One card even for a single colour, as the kit draws it. The grid is not
    // only a selector - it names the product going on the plate, which is worth
    // showing whether or not there is a second slot to choose between.
    expect(slots()).toHaveLength(1)

    await user.click(counts()[1] as HTMLElement)
    expect(slots()).toHaveLength(2)

    await user.click(counts()[3] as HTMLElement)
    expect(slots()).toHaveLength(4)
  })

  it('fills the selected slot, leaving the others untouched', async () => {
    const user = userEvent.setup()
    await configured(user)
    await user.click(counts()[1] as HTMLElement)

    const [first, second] = heldColours()
    expect(first).not.toEqual(second)

    // Blue is in neither slot yet, so it lands in the selected one and nowhere else.
    await user.click(slots()[1] as HTMLElement)
    await user.click(swatchNamed('Blue'))

    expect(heldColours()[0]).toEqual(first)
    expect(heldColours()[1]).toEqual('Blue')
  })

  it('sets the slot to the colour picked, even when another slot holds it', async () => {
    const user = userEvent.setup()
    await configured(user)
    await user.click(counts()[1] as HTMLElement)

    // Swapping instead of assigning made colours appear to cycle round the row:
    // setting the second slot to white sent the first slot's white back to
    // whatever the second slot had been holding.
    await user.click(slots()[1] as HTMLElement)
    await user.click(swatchNamed('White'))

    expect(heldColours()).toEqual(['White', 'White'])
  })

  it('never moves a slot the customer did not touch', async () => {
    const user = userEvent.setup()
    await configured(user)
    await user.click(counts()[3] as HTMLElement)
    const before = heldColours()

    await user.click(slots()[2] as HTMLElement)
    await user.click(swatchNamed('White'))

    const after = heldColours()
    expect(after[2]).toBe('White')
    // Every other slot is exactly where it was.
    expect([after[0], after[1], after[3]]).toEqual([before[0], before[1], before[3]])
  })

  it('marks a colour held by a different slot', async () => {
    const user = userEvent.setup()
    await configured(user)
    await user.click(counts()[1] as HTMLElement)

    // Slot 1 holds White, slot 2 holds Red. Standing on slot 1, Red is the one
    // that would swap — that is what the marking has to say.
    await user.click(slots()[0] as HTMLElement)
    expect(swatchNamed('Red').getAttribute('data-used')).toBe('true')
    expect(swatchNamed('White').getAttribute('data-used')).toBe('false')
    expect(swatchNamed('White').getAttribute('aria-pressed')).toBe('true')
  })

  it('survives the slot it was on disappearing', async () => {
    const user = userEvent.setup()
    await configured(user)

    await user.click(counts()[3] as HTMLElement)
    await user.click(slots()[3] as HTMLElement)
    // Dropping to two colours removes the slot the customer was standing on.
    await user.click(counts()[1] as HTMLElement)

    expect(slots()).toHaveLength(2)
    await user.click(swatchNamed('Blue'))
    expect(heldColours()).toContain('Blue')
    expect(chosenCount()).toBe(2)
  })
})

describe('«01 :: Материал»', () => {
  /** Rows of the alternatives table, as `Материал | … | Склад | Δ Цена` strings. */
  const rows = () =>
    [...document.querySelectorAll('.hv-modal tbody tr')].map((row) =>
      [...row.children].map((cell) => cell.textContent?.trim()).join(' | '),
    )

  async function alternatives(user: ReturnType<typeof userEvent.setup>) {
    await configured(user)
    await user.click(await screen.findByRole('button', { name: /Альтернативы/ }))
  }

  it('recommends by scenario and names what it picked', async () => {
    const user = userEvent.setup()
    await configured(user)

    // PLA White scores highest, and PLA is a family the shop stocks colours in.
    expect(await screen.findByText(/Подобрано :: PLA · PLA White/)).toBeTruthy()
  })

  it('compares families, not colours', async () => {
    const user = userEvent.setup()
    await alternatives(user)

    // Three scored specs, two families. Listing PLA twice would ask the customer
    // to choose between two colours of one material on tensile strength.
    expect(rows()).toHaveLength(2)
    expect(rows()[0]).toMatch(/^PLA · ПОДОБРАНО/)
    expect(rows()[1]).toMatch(/^PETG/)
  })

  it('totals a family’s stock across its colours', async () => {
    const user = userEvent.setup()
    await alternatives(user)

    // 1200 g of white and 800 g of red are both PLA on the shelf.
    expect(rows()[0]).toContain('2.0 кг')
    expect(rows()[1]).toContain('0.5 кг')
  })

  it('prices a switch off the dearest colour', async () => {
    const user = userEvent.setup()
    await alternatives(user)

    // PLA tops out at 2.40 and PETG at 2.90. Quoting PLA's cheapest colour
    // instead would advertise a +0.70 switch as +0.50 and undershoot.
    expect(rows()[1]).toContain('+ 0.50 ₽/г')
    expect(rows()[0]).toContain('± 0 ₽/г')
  })

  it('marks exactly one row as the current choice', async () => {
    const user = userEvent.setup()
    await alternatives(user)

    expect(screen.getAllByText('Выбран')).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Выбрать' })).toHaveLength(1)
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    await alternatives(user)

    await user.keyboard('{Escape}')

    expect(document.querySelector('.hv-modal')).toBeNull()
  })
})


describe('a model chosen in the catalogue', () => {
  const PICK = { slug: 'bracket-v4', code: 'BRK-004', title: 'Кронштейн', material: 'PETG' }

  /** The model well's file line, which names whatever is loaded. */
  const loaded = () =>
    document.querySelector('.hv-view__pin--bl')?.textContent?.trim()

  it('quotes it without the customer uploading anything', async () => {
    render(<ConfiguratorPage locale="ru" onCheckout={() => undefined} fromCatalog={PICK} />)

    // The whole point: a price appears with no file input touched.
    await screen.findByRole('region', { name: /цен/i })
  })

  it('names the file the farm stored, not the model title', async () => {
    render(<ConfiguratorPage locale="ru" onCheckout={() => undefined} fromCatalog={PICK} />)
    await screen.findByRole('region', { name: /цен/i })

    // From `Content-Disposition`. The title is prose and makes a poor filename,
    // and this is the name that will follow the part onto the order.
    expect(loaded()).toContain('BRACKET_V4.STL')
  })

  it('opens on the material the farm recommends for that part', async () => {
    render(<ConfiguratorPage locale="ru" onCheckout={() => undefined} fromCatalog={PICK} />)
    await screen.findByRole('region', { name: /цен/i })

    // PETG is the catalogue's recommendation and the shop carries it, so the
    // opening quote is priced on it rather than on PLA, which sorts first.
    expect(document.querySelector('.hv-slot__n')?.textContent).toContain('PETG')
  })

  it('falls back to a family the shop actually carries', async () => {
    const unstocked = { ...PICK, material: 'PEEK' }
    render(<ConfiguratorPage locale="ru" onCheckout={() => undefined} fromCatalog={unstocked} />)
    await screen.findByRole('region', { name: /цен/i })

    // A recommendation the shop cannot supply is not a plate it can print, so the
    // configurator opens on something it can.
    expect(document.querySelector('.hv-slot__n')?.textContent).toContain('PLA')
  })

  it('lets the customer replace it with their own file', async () => {
    const user = userEvent.setup()
    render(<ConfiguratorPage locale="ru" onCheckout={() => undefined} fromCatalog={PICK} />)
    await screen.findByRole('region', { name: /цен/i })

    const mine = new File([new Uint8Array([3, 4, 5])], 'mine.stl', { type: 'model/stl' })
    await user.upload(screen.getByLabelText('STL'), mine)

    // The catalogue model is a starting point, not a lock-in.
    await waitFor(() => expect(loaded()).toContain('MINE.STL'))
  })

  it('reports a model whose geometry the farm never stored', async () => {
    net.handler = (url: string) => {
      if (/\/catalog\/[^/]+\/model/.test(url)) {
        return Promise.resolve({
          ok: false,
          status: 404,
          json: () => Promise.resolve({ code: 'error.catalog.not_found' }),
        } as unknown as Response)
      }
      if (url.includes('/materials/recommend')) return Promise.resolve(jsonOk(RECOMMEND))
      if (url.includes('/materials')) return Promise.resolve(jsonOk({ rows: MATERIALS }))
      return Promise.resolve(jsonOk({}))
    }
    render(<ConfiguratorPage locale="ru" onCheckout={() => undefined} fromCatalog={PICK} />)

    // Said out loud, not swallowed into an empty configurator that looks broken.
    await screen.findByRole('alert')
  })
})
