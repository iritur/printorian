/**
 * The packing bench, on screen.
 *
 * The claims pinned here are the ones a glance cannot check and a small refactor
 * could quietly break:
 *
 * * **The recommended box is shown beside the packer's choice, never instead of
 *   it.** The moment the screen starts asserting the box rather than suggesting
 *   it, the accuracy figure it also prints becomes meaningless.
 * * **A held parcel says what is blocking it, not when the van comes.** The
 *   packer cannot act on the deadline, and a red countdown on somebody else's
 *   problem is noise pointed at the wrong person.
 * * **A parcel in hand is not coloured by its cutoff.** It is already being dealt
 *   with.
 * * **An unmeasured figure is a dash.** Zero damages and no way to record a
 *   damage are different claims, and the screen must not conflate them.
 * * **The actions need the packing permission**, which is separate from merely
 *   being allowed to look at production.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    state.handler(String(input), init)) as unknown as typeof fetch
  return state
})

import type * as UiModule from '@printorian/ui'

import { PackagingPage } from './PackagingPage'
import type { PackBoard, Parcel, TaraRow } from './types'

vi.mock('@printorian/events', () => ({ useLiveEvents: () => 'live' }))

const session = vi.hoisted(() => ({
  permissions: ['view_production', 'pack_order'],
}))

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

function aParcel(overrides: Partial<Parcel> = {}): Parcel {
  return {
    id: 'k1',
    number: 'PK-002147',
    status: 'checked',
    order_id: 'o1',
    order_number: 'ORD-2147',
    delivery_method: 'courier',
    carrier_code: '',
    cutoff_at: '2026-08-19T19:30:00Z',
    urgency: 'ok',
    minutes_to_cutoff: '270.0',
    items: 10,
    estimated_grams: '1040.00',
    length_mm: '190.0',
    width_mm: '140.0',
    height_mm: '70.0',
    volumetric_grams: '372.4',
    wrap_required: true,
    tara_id: null,
    tara_name: '',
    recommended_tara_id: 'box-a',
    recommended_tara_name: 'Коробка A',
    weight_grams: null,
    packaging_cost: '0.00',
    norm_minutes: '9.00',
    elapsed_minutes: '0.00',
    instruction_version: '2.1',
    pace_percent: null,
    projected_minutes: '9.0',
    operator_id: null,
    operator_name: '',
    started_at: null,
    finished_at: null,
    shipped_at: null,
    hold_reason: null,
    discrepancy_code: null,
    discrepancy_note: null,
    discrepancy_at: null,
    steps: [
      {
        position: 1,
        title: 'Сверить комплектность',
        detail: 'Пересчитать по цветам отдельно.',
        warning: null,
        norm_minutes: '2.00',
        actual_minutes: null,
        done_at: null,
      },
      {
        position: 2,
        title: 'Обернуть плёнкой',
        detail: null,
        warning: 'Стенки 0.6 мм — оба повреждения были на непроложенных деталях.',
        norm_minutes: '3.00',
        actual_minutes: null,
        done_at: null,
      },
    ],
    lines: [
      { model_name: 'BRACKET_V4', color: 'чёрный', ordered: 6, present: 6 },
      { model_name: 'BRACKET_V4', color: 'красный', ordered: 4, present: 4 },
    ],
    ...overrides,
  }
}

function aBoard(parcels: Parcel[] = [aParcel()], overrides: Partial<PackBoard> = {}): PackBoard {
  const columns: PackBoard['columns'] = [
    { status: 'checked', tasks: parcels.filter((one) => one.status === 'checked') },
    { status: 'packing', tasks: parcels.filter((one) => one.status === 'packing') },
    { status: 'held', tasks: parcels.filter((one) => one.status === 'held') },
    { status: 'ready', tasks: parcels.filter((one) => one.status === 'ready') },
  ]
  return {
    at: '2026-08-19T15:00:00Z',
    next_cutoff_at: '2026-08-19T19:30:00Z',
    columns,
    kpi: {
      queued: parcels.length,
      queued_by_method: [['courier', parcels.length]],
      urgent: 0,
      due_before_cutoff: parcels.length,
      packed_today: 17,
      packed_yesterday: 15,
      average_minutes: '7.8',
      norm_minutes: '9.0',
      pace_percent: '115.4',
      days_without_discrepancy: 62,
      discrepancies: 0,
      cost_per_parcel: '218.00',
    },
    tara: [
      {
        id: 'box-a',
        code: 'box-a',
        name: 'Коробка A',
        kind: 'box',
        unit: 'piece',
        inner_length_mm: '200.0',
        inner_width_mm: '150.0',
        inner_height_mm: '80.0',
        price: '62.00',
        stock: '210.00',
        reorder_at: '60.00',
        used_per_month: '88.0',
        months_left: '2.4',
      },
      {
        id: 'wrap-roll',
        code: 'wrap-roll',
        name: 'Пузырчатая плёнка',
        kind: 'wrap',
        unit: 'roll',
        inner_length_mm: null,
        inner_width_mm: null,
        inner_height_mm: null,
        price: '340.00',
        stock: '1.00',
        reorder_at: '2.00',
        used_per_month: '6.0',
        months_left: '0.2',
      },
    ],
    metrics: {
      days: 30,
      packed: 248,
      average_minutes: '7.8',
      tara_accuracy_percent: '96.0',
      discrepancies: 0,
      damages: null,
      missed_cutoffs: 1,
      cost_per_parcel: '218.00',
      score: '9.4',
    },
    shift: [
      {
        operator_id: 'u1',
        operator_name: 'i.popova@printorian',
        packed: 11,
        average_minutes: '7.4',
        discrepancies: 0,
        pace_percent: '121.6',
        score: '9.6',
        badges: [
          { code: 'badge.packing.volume', tier: 1, detail: { packed: '112', next: '250' } },
          { code: 'badge.packing.pace', tier: 3, detail: { pace: '121.6' } },
          { code: 'badge.packing.fragile', tier: 0, detail: { wrapped: '4', next: '10' } },
        ],
      },
    ],
    pickups: [{ method: 'courier', carrier_code: '', at: '2026-08-19T19:30:00Z', parcels: 3 }],
    ...overrides,
  }
}

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

function serve(board: PackBoard, onPost?: (url: string) => unknown) {
  net.handler = (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return Promise.resolve(jsonOk(onPost?.(url) ?? aParcel()))
    if (url.includes('/packaging/board')) return Promise.resolve(jsonOk(board))
    return Promise.reject(new Error(`unexpected request: ${url}`))
  }
}

beforeEach(() => {
  session.permissions = ['view_production', 'pack_order']
  serve(aBoard())
})

describe('the board', () => {
  it('keeps a parcel in hand off the urgency colours', async () => {
    serve(aBoard([aParcel({ status: 'packing', urgency: 'late', minutes_to_cutoff: '-40.0' })]))

    render(<PackagingPage locale="ru" />)

    const card = await screen.findByRole('button', { name: /PK-002147/ })
    expect(card).toHaveAttribute('data-pri', 'live')
  })

  it('says what is blocking a held parcel rather than when the van comes', async () => {
    serve(
      aBoard([
        aParcel({ status: 'held', hold_reason: 'invoice_unpaid', minutes_to_cutoff: '30.0' }),
      ]),
    )

    render(<PackagingPage locale="ru" />)

    const card = await screen.findByRole('button', { name: /PK-002147/ })
    expect(within(card).getByText(/СЧЁТ НЕ ОПЛАЧЕН/i)).toBeInTheDocument()
    expect(within(card).queryByText(/ЗАБОР/)).not.toBeInTheDocument()
  })

  it('counts down to the courier in the header', async () => {
    render(<PackagingPage locale="ru" />)

    // 15:00 to 19:30 is four and a half hours, written the way the bench says it.
    expect(await screen.findByText(/До забора курьера — 4 ч 30 м/)).toBeInTheDocument()
  })

  it('draws no countdown when no van is booked', async () => {
    serve(aBoard([aParcel()], { next_cutoff_at: null }))

    render(<PackagingPage locale="ru" />)

    expect(await screen.findByText(/Ближайший забор не назначен/)).toBeInTheDocument()
  })
})

describe('the tara table', () => {
  it('marks a position at or under its reorder level', async () => {
    render(<PackagingPage locale="ru" />)

    const row = (await screen.findByText('Пузырчатая плёнка')).closest('tr') as HTMLElement
    expect(within(row).getByText('1')).toHaveClass('hv-warn')
  })

  it('stops counting months once the figure is arithmetic rather than information', async () => {
    // Two years of cover and seventeen call for the same decision, and the large
    // figure mostly says the farm has barely used the item yet.
    const [box] = aBoard().tara as [TaraRow, ...TaraRow[]]
    serve(aBoard([aParcel()], { tara: [{ ...box, months_left: '209.0' }] }))

    render(<PackagingPage locale="ru" />)

    const row = (await screen.findByText('Коробка A')).closest('tr') as HTMLElement
    expect(within(row).getByText('> 24 мес')).toBeInTheDocument()
  })

  it('writes cover under a month in days, which is a number somebody acts on', async () => {
    render(<PackagingPage locale="ru" />)

    const row = (await screen.findByText('Пузырчатая плёнка')).closest('tr') as HTMLElement
    expect(within(row).getByText('6 дн.')).toBeInTheDocument()
  })
})

describe('figures nobody has measured', () => {
  it('prints a dash for damages rather than a zero', async () => {
    render(<PackagingPage locale="ru" />)

    const row = (await screen.findByText('Повреждений в пути')).closest('li') as HTMLElement
    expect(within(row).getByText('—')).toBeInTheDocument()
  })

  it('says so plainly when nothing has shipped', async () => {
    serve(
      aBoard([aParcel()], {
        kpi: { ...aBoard().kpi, days_without_discrepancy: null },
      }),
    )

    render(<PackagingPage locale="ru" />)

    expect(await screen.findByText(/ИЗМЕРЯТЬ НЕЧЕГО/)).toBeInTheDocument()
  })
})

describe('the parcel popup', () => {
  it('shows the recommended box beside what was actually used', async () => {
    render(<PackagingPage locale="ru" />)
    await userEvent.click(await screen.findByRole('button', { name: /PK-002147/ }))

    const dialog = await screen.findByRole('dialog')
    const row = within(dialog).getByText('Рекомендуемая тара').closest('li') as HTMLElement

    expect(within(row).getByText('Коробка A')).toBeInTheDocument()
    // A suggestion, not an assertion: the select is still the packer's to change.
    expect(within(dialog).getByLabelText('Тара')).toBeInTheDocument()
  })

  it('states the volumetric weight beside the real one', async () => {
    render(<PackagingPage locale="ru" />)
    await userEvent.click(await screen.findByRole('button', { name: /PK-002147/ }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('1 040 г')).toBeInTheDocument()
    expect(within(dialog).getByText('372 г')).toBeInTheDocument()
  })

  it('lists what the customer bought, split by colour', async () => {
    render(<PackagingPage locale="ru" />)
    await userEvent.click(await screen.findByRole('button', { name: /PK-002147/ }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/BRACKET_V4 · чёрный/)).toBeInTheDocument()
    expect(within(dialog).getByText(/BRACKET_V4 · красный/)).toBeInTheDocument()
    expect(within(dialog).getByText('ИТОГО 10 ДЕТАЛЕЙ')).toBeInTheDocument()
  })

  it('shows the step norm before the step is started', async () => {
    render(<PackagingPage locale="ru" />)
    await userEvent.click(await screen.findByRole('button', { name: /PK-002147/ }))

    const dialog = await screen.findByRole('dialog')
    const step = within(dialog)
      .getByText('Сверить комплектность')
      .closest('.hv-step') as HTMLElement
    expect(within(step).getByText('2 м')).toBeInTheDocument()
  })

  it('takes the parcel on through the API rather than guessing the new state', async () => {
    const posted: string[] = []
    serve(aBoard(), (url) => {
      posted.push(url)
      return aParcel({ status: 'packing' })
    })

    render(<PackagingPage locale="ru" />)
    await userEvent.click(await screen.findByRole('button', { name: /PK-002147/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Взять в работу' }))

    await waitFor(() => expect(posted.some((url) => url.endsWith('/start'))).toBe(true))
  })

  it('offers nothing to act with when the actor may only look', async () => {
    session.permissions = ['view_production']

    render(<PackagingPage locale="ru" />)
    await userEvent.click(await screen.findByRole('button', { name: /PK-002147/ }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByRole('button', { name: 'Взять в работу' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Отложить' })).not.toBeInTheDocument()
  })
})

describe('the marks', () => {
  it('shows an unearned badge dimmed rather than hiding it', async () => {
    render(<PackagingPage locale="ru" />)

    const badge = (await screen.findByText('Хрупкий груз')).closest('.hv-badge') as HTMLElement
    expect(badge).toHaveAttribute('data-tier', '0')
    expect(within(badge).getByText('4 ИЗ 10')).toBeInTheDocument()
  })

  it('says plainly that nothing here is awarded by hand', async () => {
    render(<PackagingPage locale="ru" />)

    expect(await screen.findByText(/РУЧНОГО НАЗНАЧЕНИЯ НЕТ/)).toBeInTheDocument()
  })
})
