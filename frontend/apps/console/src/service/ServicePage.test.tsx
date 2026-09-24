/**
 * The service screen, asserted at the screen.
 *
 * * **A driver-opened ticket is named by its origin, not by an empty title.**
 *   The server sends `title: ""` for those (ADR-0012), and a card that read as
 *   blank would be the badge the kit draws lost.
 * * **A lane comes from the server.** The card is drawn in the lane the response
 *   put it in, never re-sorted by kind here; a repair being worked shows under
 *   «В работе».
 * * **Nulls on the reliability table are dashes, never `0`.** An unobserved
 *   machine's rate is an absence (ADR-0007).
 * * **The close button is disabled while a step is unticked**, mirroring the
 *   server's `error.service.steps_pending`, and ticking the step sends the
 *   step's own position.
 * * **An operator without `operate_printer` sees the board and no controls.**
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
    seen: [] as { url: string; method: string; body: unknown }[],
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    state.seen.push({
      url: String(input),
      method: init?.method ?? 'GET',
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : null,
    })
    return state.handler(String(input), init)
  }) as unknown as typeof fetch
  return state
})

import type * as UiModule from '@printorian/ui'

import { ServicePage } from './ServicePage'
import type { ReliabilityReport, Ticket, TicketBoardView } from './types'

const session = vi.hoisted(() => ({ permissions: ['view_production', 'operate_printer'] }))

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

function aTicket(overrides: Partial<Ticket> = {}): Ticket {
  return {
    id: 't1',
    number: 'SV-000412',
    kind: 'repair',
    status: 'open',
    origin: 'driver',
    printer_id: 'p4',
    failure_id: 'f1',
    title: '',
    note: null,
    norm_minutes: 35,
    opened_at: '2026-09-24T09:00:00Z',
    started_at: null,
    closed_at: null,
    opened_by: null,
    assignee_id: null,
    elapsed_seconds: 41 * 60,
    steps: [
      {
        position: 1,
        title: 'Остановить печать',
        note: null,
        norm_minutes: 2,
        done_at: null,
        done_by: null,
      },
    ],
    steps_done: 0,
    ...overrides,
  }
}

function aBoard(overrides: Partial<TicketBoardView['board']> = {}): TicketBoardView {
  return {
    board: {
      emergency: [aTicket()],
      planned: [],
      in_progress: [
        aTicket({
          id: 't2',
          number: 'SV-000416',
          kind: 'install',
          status: 'in_progress',
          origin: 'person',
          title: 'установка новой машины',
          printer_id: 'p13',
          elapsed_seconds: 28 * 60,
          steps: [],
        }),
      ],
      logistics: [],
      closed: [],
      closed_since: '2026-09-23T09:41:00Z',
      ...overrides,
    },
    printers: [
      { id: 'p4', name: 'P-04', is_active: true },
      { id: 'p13', name: 'P-13', is_active: true },
    ],
    at: '2026-09-24T09:41:00Z',
  }
}

function aReport(): ReliabilityReport {
  return {
    rows: [
      {
        printer_id: 'p4',
        printer_name: 'P-04',
        state: 'error',
        failures: 7,
        closed_failures: 6,
        open_failures: 1,
        observed_seconds: '3600000',
        failures_per_1000_hours: '7.00',
        mttr_minutes: '34.5',
      },
      {
        printer_id: 'p8',
        printer_name: 'P-08',
        state: 'offline',
        failures: 0,
        closed_failures: 0,
        open_failures: 0,
        observed_seconds: null,
        failures_per_1000_hours: null,
        mttr_minutes: null,
      },
    ],
    causes: [{ cause: 'filament_break', count: 6 }],
    uncategorised: 1,
    printers_reporting: 1,
    printers_listed: 2,
  }
}

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

function serve(board: TicketBoardView, detail: Ticket = aTicket()) {
  net.handler = (url: string, init?: RequestInit) => {
    if (url.includes('/service/reliability')) return Promise.resolve(jsonOk(aReport()))
    // A POST to the collection is «Создать заявку» and answers with the ticket;
    // the same URL read is the board. Method first, or the raise gets a board.
    if (init?.method === 'POST') return Promise.resolve(jsonOk(detail))
    if (url.endsWith('/service/tickets')) return Promise.resolve(jsonOk(board))
    if (url.includes('/service/tickets/')) return Promise.resolve(jsonOk(detail))
    return Promise.reject(new Error(`unexpected request: ${url}`))
  }
}

beforeEach(() => {
  session.permissions = ['view_production', 'operate_printer']
  net.seen.length = 0
  serve(aBoard())
})

describe('the board', () => {
  it('draws each card in the lane the server put it in, and names a driver ticket by its origin', async () => {
    render(<ServicePage locale="ru" />)

    const emergency = (await screen.findByText('Аварийные')).closest('[data-lane]')
    expect(emergency).not.toBeNull()
    const card = within(emergency as HTMLElement)
      .getByText('SV-000412')
      .closest('li')
    expect(card?.textContent).toMatch(/P-04 · Сообщил драйвер/)
    expect(card?.textContent).toMatch(/41 м/)

    const working = screen.getByText('В работе').closest('[data-lane]')
    expect(within(working as HTMLElement).getByText('SV-000416')).toBeInTheDocument()
    expect(within(working as HTMLElement).queryByText('SV-000412')).toBeNull()
  })

  it('draws an unobserved machine with dashes, never zero', async () => {
    render(<ServicePage locale="ru" />)

    const row = (await screen.findByText('P-08')).closest('tr')
    const cells = within(row as HTMLElement).getAllByRole('cell')
    expect(cells[2]?.textContent).toBe('0')
    expect(cells[3]?.textContent).toBe('—')
    expect(cells[4]?.textContent).toBe('—')
    const observed = (await screen.findByText('P-04')).closest('tr')
    expect(observed?.textContent).toMatch(/7\.00/)
    expect(screen.getByText(/ПРИЧИН НАЗВАНО: 6 · БЕЗ ПРИЧИНЫ: 1/)).toBeInTheDocument()
  })

  it('hides the controls from somebody who may only look', async () => {
    session.permissions = ['view_production']

    render(<ServicePage locale="ru" />)

    await screen.findByText('SV-000412')
    expect(screen.queryByRole('button', { name: 'Создать заявку' })).toBeNull()
  })
})

describe('one ticket', () => {
  it('cannot be closed with a step unticked, and ticking sends the step position', async () => {
    const ticked = aTicket({
      status: 'in_progress',
      steps: [
        {
          position: 1,
          title: 'Остановить печать',
          note: null,
          norm_minutes: 2,
          done_at: '2026-09-24T09:50:00Z',
          done_by: 'u1',
        },
      ],
      steps_done: 1,
    })
    net.handler = (url: string, init?: RequestInit) => {
      if (url.includes('/service/reliability')) return Promise.resolve(jsonOk(aReport()))
      if (url.endsWith('/service/tickets')) return Promise.resolve(jsonOk(aBoard()))
      if (url.endsWith('/steps/1/done') && init?.method === 'POST')
        return Promise.resolve(jsonOk(ticked))
      if (url.includes('/service/tickets/t1')) return Promise.resolve(jsonOk(aTicket()))
      return Promise.reject(new Error(`unexpected request: ${url}`))
    }

    render(<ServicePage locale="ru" />)
    await userEvent.click(await screen.findByText('SV-000412'))

    const dialog = await screen.findByRole('dialog')
    const close = within(dialog).getByRole('button', { name: 'Закрыть заявку' })
    expect(close).toBeDisabled()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Сделано' }))

    await waitFor(() =>
      expect(net.seen.some((call) => call.url.endsWith('/service/tickets/t1/steps/1/done'))).toBe(
        true,
      ),
    )
    expect(within(dialog).getByText('СДЕЛАНО 1 ИЗ 1')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Закрыть заявку' })).toBeEnabled()
  })

  it('raises a ticket with the kind and title typed', async () => {
    render(<ServicePage locale="ru" />)

    await userEvent.click(await screen.findByRole('button', { name: 'Создать заявку' }))
    await userEvent.selectOptions(screen.getByLabelText('Вид заявки'), 'move')
    await userEvent.type(screen.getByLabelText('Что нужно сделать'), 'Снять партию с P-01')
    await userEvent.click(screen.getByRole('button', { name: 'Создать' }))

    await waitFor(() => {
      const post = net.seen.find(
        (call) => call.method === 'POST' && call.url.endsWith('/service/tickets'),
      )
      expect(post?.body).toEqual({ kind: 'move', title: 'Снять партию с P-01', printer_id: null })
    })
  })
})
