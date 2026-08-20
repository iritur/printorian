/**
 * An operator's shift.
 *
 * The claims pinned here are the ones a glance cannot check and a small refactor
 * could quietly break:
 *
 * * **The norm is visible before the step is started.** That is the whole reason
 *   the screen exists; a norm you only meet when you miss it is a stick.
 * * **A card in hand is not coloured by its deadline.** The operator is already
 *   on it, and a red stripe there would point at the one task being dealt with.
 * * **A drying batch shows when it is ready, not when the order is due.** Nobody
 *   may touch it before then, so an urgency chip would be an instruction to do
 *   something forbidden.
 * * **An unearned badge is shown, dimmed.** Hiding it leaves nothing to earn.
 * * **Inspection buttons need the QC permission**, which is separate from the one
 *   that advances a task.
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

import { PostProductionPage } from './PostProductionPage'
import type { Board, Task } from './types'

vi.mock('@printorian/events', () => ({ useLiveEvents: () => 'live' }))

const session = vi.hoisted(() => ({
  permissions: ['view_production', 'advance_postproduction', 'record_qc'],
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

function aTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 't1',
    number: 'PP-000001',
    status: 'waiting',
    kind: 'sanding',
    order_id: 'o1',
    order_number: 'ORD-2147',
    model_name: 'BRACKET_V4',
    material_code: 'PETG-CF',
    colors: ['#1b1b1e'],
    printer_id: 'p1',
    quantity: 10,
    due_at: '2026-08-20T18:00:00Z',
    urgency: 'ok',
    minutes_to_due: '600.0',
    norm_minutes: '40.00',
    elapsed_minutes: '0.00',
    instruction_version: '4.2',
    pace_percent: null,
    projected_minutes: '29.0',
    operator_id: null,
    operator_name: '',
    started_at: null,
    finished_at: null,
    cure_until: null,
    attempt: 1,
    defect_code: null,
    defect_note: null,
    steps: [
      {
        position: 1,
        title: 'Снять поддержки',
        detail: '10 деталей.',
        warning: null,
        norm_minutes: '3.00',
        actual_minutes: null,
        done_at: null,
      },
      {
        position: 2,
        title: 'Чистовая шлифовка P400',
        detail: null,
        warning: 'Две стенки 0.6 мм — не давить.',
        norm_minutes: '14.00',
        actual_minutes: null,
        done_at: null,
      },
    ],
    ...overrides,
  }
}

function aBoard(tasks: Task[] = [aTask()], overrides: Partial<Board> = {}): Board {
  const columns: Board['columns'] = [
    { status: 'waiting', tasks: tasks.filter((task) => task.status === 'waiting') },
    { status: 'in_progress', tasks: tasks.filter((task) => task.status === 'in_progress') },
    { status: 'paused', tasks: tasks.filter((task) => task.status === 'paused') },
    { status: 'curing', tasks: tasks.filter((task) => task.status === 'curing') },
    { status: 'for_qc', tasks: tasks.filter((task) => task.status === 'for_qc') },
    { status: 'returned', tasks: tasks.filter((task) => task.status === 'returned') },
  ]
  return {
    at: '2026-08-19T15:00:00Z',
    columns,
    kpi: {
      queued: tasks.length,
      queued_by_kind: [['sanding', tasks.length]],
      urgent: 0,
      completed_today: 11,
      completed_yesterday: 8,
      quality_percent: '98.4',
      returns: 1,
      pace_percent: '104.0',
      shop_pace_percent: '99.0',
    },
    operations: [
      {
        kind: 'sanding',
        completed: 148,
        norm_minutes: '3552.0',
        actual_minutes: '3283.0',
        pace_percent: '108.2',
        returns: 2,
      },
    ],
    shift: [
      {
        operator_id: 'u1',
        operator_name: 'a.smirnov@printorian',
        completed: 31,
        returns: 0,
        pace_percent: '104.0',
        score: '9.2',
        is_trainee: false,
        badges: [
          { code: 'badge.volume', tier: 2, detail: { completed: '31', next: '100' } },
          { code: 'badge.pace', tier: 1, detail: { pace: '104.0' } },
          { code: 'badge.mastery.painting', tier: 0, detail: { completed: '0', next: '25' } },
        ],
      },
    ],
    consumables: [
      {
        id: 'c1',
        code: 'gloves-m',
        name: 'Перчатки нитрил M',
        unit: 'pair',
        remaining: '0',
        reorder_at: '10',
      },
    ],
    output_by_day: Array.from({ length: 14 }, (_, index) => [
      `2026-08-${String(6 + index).padStart(2, '0')}T00:00:00Z`,
      index === 13 ? 16 : 11,
    ]),
    ...overrides,
  }
}

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

function serve(board: Board, onPost?: (url: string) => unknown) {
  net.handler = (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return Promise.resolve(jsonOk(onPost?.(url) ?? aTask()))
    if (url.includes('/postproduction/board')) return Promise.resolve(jsonOk(board))
    return Promise.reject(new Error(`unexpected request: ${url}`))
  }
}

beforeEach(() => {
  session.permissions = ['view_production', 'advance_postproduction', 'record_qc']
  serve(aBoard())
})

describe('the board', () => {
  it('keeps a card in hand off the urgency colours', async () => {
    serve(aBoard([aTask({ status: 'in_progress', urgency: 'late', minutes_to_due: '-40.0' })]))

    render(<PostProductionPage locale="ru" />)

    const card = await screen.findByRole('button', { name: /PP-000001/ })
    // Late, and being worked on. `live`, not `rush` — a red stripe on the one
    // task somebody is actually dealing with points at the wrong thing.
    expect(card).toHaveAttribute('data-pri', 'live')
  })

  it('marks a card whose promise has passed', async () => {
    serve(aBoard([aTask({ urgency: 'late', minutes_to_due: '-40.0' })]))

    render(<PostProductionPage locale="ru" />)

    const card = await screen.findByRole('button', { name: /PP-000001/ })
    expect(card).toHaveAttribute('data-pri', 'rush')
    expect(within(card).getByText(/ПРОСРОЧКА 40 м/)).toBeInTheDocument()
  })

  it('shows a drying batch when it is ready, not when the order is due', async () => {
    serve(
      aBoard([
        aTask({
          status: 'curing',
          urgency: 'late',
          minutes_to_due: '-90.0',
          cure_until: '2026-08-19T17:22:00Z',
        }),
      ]),
    )

    render(<PostProductionPage locale="ru" />)

    const card = await screen.findByRole('button', { name: /PP-000001/ })
    expect(within(card).getByText(/ГОТОВО В/)).toBeInTheDocument()
    expect(within(card).queryByText(/ПРОСРОЧКА/)).not.toBeInTheDocument()
  })

  it('keeps every column present even when empty', async () => {
    render(<PostProductionPage locale="ru" />)
    await screen.findByRole('button', { name: /PP-000001/ })

    // Position is how the board is read from two metres away; columns that come
    // and go make that impossible.
    for (const label of ['Ожидает', 'В работе', 'Сушка · выдержка', 'На контроль']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })
})

describe('the instruction', () => {
  it('shows every step norm before the step is started', async () => {
    render(<PostProductionPage locale="ru" />)

    await userEvent.click(await screen.findByRole('button', { name: /PP-000001/ }))

    const dialog = await screen.findByRole('dialog')
    // Nothing has been ticked, and both norms are already on screen.
    expect(within(dialog).getByText('3 МИН')).toBeInTheDocument()
    expect(within(dialog).getByText('14 МИН')).toBeInTheDocument()
    expect(within(dialog).queryByText(/ФАКТ/)).not.toBeInTheDocument()
  })

  it('carries the warning that names what causes returns', async () => {
    render(<PostProductionPage locale="ru" />)

    await userEvent.click(await screen.findByRole('button', { name: /PP-000001/ }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Две стенки 0.6 мм/)).toBeInTheDocument()
  })

  it('shows the fact beside the norm once a step is ticked', async () => {
    const worked = aTask({
      status: 'in_progress',
      elapsed_minutes: '4.00',
      steps: [
        {
          position: 1,
          title: 'Снять поддержки',
          detail: null,
          warning: null,
          norm_minutes: '3.00',
          actual_minutes: '4.00',
          done_at: '2026-08-19T14:04:00Z',
        },
      ],
    })
    serve(aBoard([worked]))

    render(<PostProductionPage locale="ru" />)
    await userEvent.click(await screen.findByRole('button', { name: /PP-000001/ }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/3 МИН/)).toBeInTheDocument()
    expect(within(dialog).getByText(/ФАКТ 4/)).toBeInTheDocument()
  })
})

describe('permissions', () => {
  it('offers inspection only to whoever may record quality control', async () => {
    session.permissions = ['view_production', 'advance_postproduction']
    serve(aBoard([aTask({ status: 'for_qc' })]))

    render(<PostProductionPage locale="ru" />)
    await userEvent.click(await screen.findByRole('button', { name: /PP-000001/ }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByRole('button', { name: 'Принять' })).not.toBeInTheDocument()
  })

  it('offers it to somebody who holds the permission', async () => {
    serve(aBoard([aTask({ status: 'for_qc' })]))

    render(<PostProductionPage locale="ru" />)
    await userEvent.click(await screen.findByRole('button', { name: /PP-000001/ }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Принять' })).toBeInTheDocument()
  })
})

describe('the marks', () => {
  it('shows an unearned badge dimmed rather than hiding it', async () => {
    const { container } = render(<PostProductionPage locale="ru" />)
    await screen.findByRole('button', { name: /PP-000001/ })

    const badges = container.querySelectorAll('.hv-badge')
    // Three badges, one of them at tier 0 — there has to be something to earn.
    expect(badges).toHaveLength(3)
    expect(container.querySelector('.hv-badge[data-tier="0"]')).not.toBeNull()
  })

  it('says plainly that nothing here is awarded by hand', async () => {
    render(<PostProductionPage locale="ru" />)
    await screen.findByRole('button', { name: /PP-000001/ })

    expect(screen.getByText(/РУЧНОГО НАЗНАЧЕНИЯ НЕТ/)).toBeInTheDocument()
  })
})

describe('returning a batch', () => {
  it('asks for a reason before it will send one back', async () => {
    const posts: string[] = []
    serve(aBoard([aTask({ status: 'for_qc' })]), (url) => {
      posts.push(url)
      return aTask({ status: 'returned', attempt: 2 })
    })

    render(<PostProductionPage locale="ru" />)
    await userEvent.click(await screen.findByRole('button', { name: /PP-000001/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Сообщить о браке' }))

    // The defect picker appears first; nothing has been sent yet.
    expect(await screen.findByLabelText('Код дефекта')).toBeInTheDocument()
    expect(posts).toHaveLength(0)

    await userEvent.click(screen.getByRole('button', { name: 'Отправить на повтор' }))
    await waitFor(() => expect(posts.some((url) => url.endsWith('/return'))).toBe(true))
  })
})
