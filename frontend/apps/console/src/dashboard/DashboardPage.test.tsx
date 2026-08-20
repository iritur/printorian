/**
 * The farm summary.
 *
 * What is pinned here is the set of claims the screen makes that a glance cannot
 * check, and that a small refactor could quietly break:
 *
 * * **Direction is not sentiment.** Spend rising is red and revenue rising is
 *   green, and both carry an up arrow. Getting this backwards makes a dashboard
 *   congratulate a farm on its costs, and it looks correct while it does it.
 * * **A machine's square shows state, never a leftover percentage.** Progress on
 *   an idle machine is the remains of the last job, which reads as work in hand.
 * * **A farm that printed nothing has no success rate.** 100% for zero prints is
 *   the most misleading figure a summary can carry.
 * * **The filament bar is scaled to the larger of held and promised**, so an
 *   over-committed material overflows visibly instead of being clipped to full.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The fetch stub is installed before the imports run.
 *
 * `ApiClient` binds `globalThis.fetch` in its constructor and the session module
 * constructs one at module scope, so a stub installed in `beforeEach` would
 * arrive after the real `fetch` had already been captured.
 */
const net = vi.hoisted(() => {
  const state = {
    handler: (() => Promise.reject(new Error('no handler'))) as (url: string) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL) =>
    state.handler(String(input))) as unknown as typeof fetch
  return state
})

import type * as UiModule from '@printorian/ui'

import { DashboardPage } from './DashboardPage'
import type { FarmSummary, Trend } from './types'

vi.mock('@printorian/events', () => ({
  // The screen's live behaviour is the fleet table's, already covered there.
  // Here the stream is inert so a test asserts about one fetched summary.
  useLiveEvents: () => 'live',
}))

vi.mock('@printorian/ui', async () => {
  const actual = await vi.importActual<typeof UiModule>('@printorian/ui')
  return {
    ...actual,
    useSession: () => ({
      ready: true,
      actor: { permissions: ['view_all_orders'] },
    }),
  }
})

const trend = (value: string, previous: string, change: string | null): Trend => ({
  value,
  previous,
  change_percent: change,
})

function aSummary(overrides: Partial<FarmSummary> = {}): FarmSummary {
  return {
    at: '2026-08-19T15:00:00Z',
    window: {
      period: 'today',
      start: '2026-08-19T00:00:00Z',
      end: '2026-08-19T15:00:00Z',
      previous_start: '2026-08-18T09:00:00Z',
    },
    orders: {
      placed: trend('14', '11', '27.3'),
      placed_month: trend('248', '227', '9.3'),
      paid: 12,
      awaiting_payment: 2,
      in_progress: 18,
      funnel: [
        { status: 'printing', count: 7 },
        { status: 'packing', count: 3 },
        { status: 'queued', count: 0 },
      ],
      average_order: trend('6840', '6580', '4.0'),
      median_order: '4210',
      lines_per_order: '7.2',
    },
    finance: {
      received: trend('1240000', '1107000', '12.0'),
      spend: trend('781000', '723000', '8.0'),
      profit: trend('459000', '384000', '19.5'),
      margin_percent: '37.0',
      received_today: '86400',
      spend_today: '52100',
      receivable: '32400',
      refund_count: 4,
      refund_total: '18200',
      spend_by_category: [
        { category: 'material', amount: '297000' },
        { category: 'labor', amount: '242000' },
        { category: 'risk', amount: '0' },
      ],
      revenue_by_day: Array.from({ length: 30 }, (_, index) => ({
        day: `2026-07-${String(21 + index).padStart(2, '0')}T00:00:00Z`,
        amount: index === 29 ? '86400' : '12100',
      })),
    },
    fleet: {
      zones: [
        {
          name: 'ЦЕХ A',
          load_percent: '50.0',
          nodes: [
            {
              id: 'p1',
              name: 'P-01',
              state: 'printing',
              progress_percent: 63,
              eta: '2026-08-19T18:00:00Z',
              current_job: 'BRACKET_V4',
              needs_attention: false,
              maintenance_due: false,
              last_seen_at: '2026-08-19T14:59:00Z',
            },
            {
              // Deliberately carries a stale percentage while idle.
              id: 'p2',
              name: 'P-02',
              state: 'idle',
              progress_percent: 100,
              eta: null,
              current_job: null,
              needs_attention: false,
              maintenance_due: false,
              last_seen_at: '2026-08-19T14:59:00Z',
            },
          ],
        },
      ],
      counts: [
        { state: 'printing', count: 1 },
        { state: 'idle', count: 1 },
      ],
      total: 2,
      printing: 1,
      attention: 0,
      utilisation_percent: '50.0',
      throughput: {
        run_hours: '18.0',
        capacity_hours: '30.0',
        idle_hours: '12.0',
        succeeded: 9,
        failed: 1,
        success_percent: '90.0',
        truncated: false,
      },
      hourly_load: Array.from({ length: 7 }, (_, day) => ({
        weekday: day,
        hours: Array.from({ length: 24 }, (_, hour) => (hour < 6 ? '0.20' : '0.80')),
      })),
    },
    schedule: {
      starts_at: '2026-08-19T15:00:00Z',
      ends_at: '2026-08-20T03:00:00Z',
      rows: [
        {
          printer_id: 'p1',
          free_at: '2026-08-19T18:00:00Z',
          bars: [
            {
              job_id: 'j1',
              order_id: 'o1',
              order_number: 'ORD-2147',
              status: 'printing',
              label: 'BRACKET_V4',
              starts_at: '2026-08-19T15:00:00Z',
              ends_at: '2026-08-19T18:00:00Z',
              progress_percent: 63,
            },
          ],
        },
      ],
    },
    filament: [
      {
        code: 'PETG-CF-BLACK',
        name: 'PETG-CF чёрный',
        color_hex: '#1b1b1e',
        loaded_grams: '800',
        stock_grams: '1000',
        committed_grams: '400',
        free_grams: '1400',
        committed_jobs: 3,
        loaded_printer_ids: ['p1'],
      },
    ],
    alerts: [],
    wait_list: 0,
    ...overrides,
  }
}

/** `ApiClient` reads `ok`, `status` and `json()`; nothing else is needed here. */
function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

function serve(summary: FarmSummary, printer: Record<string, unknown> = {}) {
  net.handler = (url: string) => {
    if (url.includes('/dashboard')) return Promise.resolve(jsonOk(summary))
    if (url.includes('/printers/')) return Promise.resolve(jsonOk(printer))
    return Promise.reject(new Error(`unexpected request: ${url}`))
  }
}

beforeEach(() => {
  serve(aSummary())
})

describe('the finance tiles', () => {
  it('paints rising spend red and rising revenue green', async () => {
    render(<DashboardPage locale="ru" />)

    const received = await screen.findByText('Поступило')
    const spend = screen.getByText('Расходы')

    const receivedDelta = within(received.closest('.hv-kpi') as HTMLElement).getByText('+12%')
    const spendDelta = within(spend.closest('.hv-kpi') as HTMLElement).getByText('+8%')

    // Both went up. Only one of them is good news.
    expect(receivedDelta).toHaveAttribute('data-dir', 'up')
    expect(spendDelta).toHaveAttribute('data-dir', 'up')
    expect(receivedDelta).toHaveAttribute('data-sentiment', 'good')
    expect(spendDelta).toHaveAttribute('data-sentiment', 'bad')
  })

  it('says nothing about a change it cannot compute', async () => {
    const summary = aSummary()
    summary.finance.received = trend('1000', '0', null)
    serve(summary)

    render(<DashboardPage locale="ru" />)

    const received = await screen.findByText('Поступило')
    const tile = received.closest('.hv-kpi') as HTMLElement
    expect(tile.querySelector('.hv-kpi__d')).toBeNull()
  })

  it('omits a category nothing was spent on', async () => {
    render(<DashboardPage locale="ru" />)

    expect(await screen.findByText('Материал')).toBeInTheDocument()
    expect(screen.queryByText('Резерв на брак')).not.toBeInTheDocument()
  })
})

describe('the status wall', () => {
  it('shows a percentage only for a machine that is running', async () => {
    render(<DashboardPage locale="ru" />)

    const printing = await screen.findByRole('button', { name: /P-01 · Печатает/ })
    const idle = screen.getByRole('button', { name: /P-02 · Свободен/ })

    expect(within(printing).getByText('63%')).toBeInTheDocument()
    // P-02 carries `progress_percent: 100` and is idle. The leftover must not
    // be presented as work in hand.
    expect(within(idle).queryByText('100%')).not.toBeInTheDocument()
    expect(within(idle).getByText('Свободен')).toBeInTheDocument()
  })

  it('opens the machine when its square is clicked', async () => {
    serve(aSummary(), {
      id: 'p1',
      name: 'P-01',
      state: 'printing',
      brand: 'bambu',
      model: 'P1S',
      serial: 'X',
      connection_mode: 'lan',
      host: null,
      access_code_set: false,
      location: null,
      last_seen_at: null,
      progress_percent: 63,
      eta: null,
      current_job: null,
      needs_attention: false,
      maintenance_due: false,
      printed_hours: '120',
      amortization_per_hour: '10',
      services: [],
    })
    render(<DashboardPage locale="ru" />)

    await userEvent.click(await screen.findByRole('button', { name: /P-01/ }))

    // The wall carries only what a square needs; the popup is read on demand.
    // Asserted on the dialog rather than on a heading inside it: the machine's
    // name lives in the popup's chrome now, and "a dialog for P-01 opened" is
    // the claim worth making either way.
    await waitFor(() =>
      expect(screen.getByRole('dialog', { name: /P-01/ })).toBeInTheDocument(),
    )
  })
})

describe('the printer KPIs', () => {
  it('refuses to claim a success rate when nothing finished', async () => {
    const summary = aSummary()
    summary.fleet.throughput = {
      run_hours: '0.0',
      capacity_hours: '30.0',
      idle_hours: '30.0',
      succeeded: 0,
      failed: 0,
      success_percent: null,
      truncated: false,
    }
    serve(summary)

    render(<DashboardPage locale="ru" />)

    const efficiency = await screen.findByText('Эффективность')
    const tile = efficiency.closest('.hv-kpi') as HTMLElement
    expect(within(tile).getByText('—')).toBeInTheDocument()
    expect(within(tile).getByText('ЗА ПЕРИОД НИЧЕГО НЕ ЗАВЕРШЕНО')).toBeInTheDocument()
  })
})

describe('the filament panel', () => {
  it('scales the bar to the promise when more is promised than held', async () => {
    const summary = aSummary()
    summary.filament = [
      {
        code: 'PETG-CF-RED',
        name: 'PETG-CF красный',
        color_hex: '#d64545',
        loaded_grams: '340',
        stock_grams: '0',
        committed_grams: '1000',
        free_grams: '-660',
        committed_jobs: 4,
        loaded_printer_ids: ['p1'],
      },
    ]
    serve(summary)

    const { container } = render(<DashboardPage locale="ru" />)
    await screen.findByText(/PETG-CF красный/)

    const bar = container.querySelector('.hv-stack-bar') as HTMLElement
    const loaded = bar.querySelector('[data-part="loaded"]') as HTMLElement
    const committed = bar.querySelector('[data-part="committed"]') as HTMLElement

    // Scaled to the 1000 g promised, not the 340 g held: the overshoot has to be
    // visible, and scaling to the holding would draw both parts full.
    expect(loaded.style.width).toBe('34%')
    expect(committed.style.width).toBe('100%')
    expect(screen.getByText(/НЕ ХВАТАЕТ 660 Г/)).toBeInTheDocument()
  })
})

describe('the period switch', () => {
  it('asks the server for the new window rather than re-slicing the old one', async () => {
    const asked: string[] = []
    net.handler = (url: string) => {
      if (url.includes('/dashboard')) {
        asked.push(url)
        return Promise.resolve(jsonOk(aSummary()))
      }
      return Promise.reject(new Error(url))
    }

    render(<DashboardPage locale="ru" />)
    await screen.findByText('Заказы')

    await userEvent.click(screen.getByRole('button', { name: 'Месяц' }))

    // The comparison window is the server's to cut — a client that re-sliced a
    // fetched "today" into a "month" would be inventing the previous period.
    await waitFor(() => expect(asked.some((url) => url.includes('period=month'))).toBe(true))
  })
})
