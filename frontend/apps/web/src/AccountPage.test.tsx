/**
 * The account screen's two claims that a browser check would not catch.
 *
 * **Absent is not zero.** The header's four plates report measurements, and a
 * customer who has never had an order dispatched has no average lead time. A `0`
 * there reads as "we shipped it instantly", which is the opposite of the truth,
 * and it is the kind of regression that looks fine on a populated dev database
 * and only appears in front of a new customer.
 *
 * **The badge is the discount.** «Silver · −4%» is drawn from the same ladder
 * the pricing engine is handed, so the two cannot say different things — but the
 * *rendering* of it (which rungs are lit, what the gap says, how far the bar is
 * filled) is arithmetic this screen does alone, and it is arithmetic against a
 * figure the customer can check.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// Installed before the imports: `ApiClient` binds `globalThis.fetch` in its
// constructor and the session module builds one at module scope, so a stub
// installed in `beforeEach` would arrive after the real one was captured.
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

import { AccountPage } from './AccountPage'
import { SessionProvider } from '@printorian/ui'

const ACTOR = {
  user_id: 'u1',
  email: 'boss@printorian.example',
  display_name: 'Дмитрий Чудинов',
  role: 'customer',
  locale: 'ru',
  permissions: [],
}

const PROFILE = {
  id: 'u1',
  email: 'boss@printorian.example',
  display_name: 'Дмитрий Чудинов',
  role: 'customer',
  locale: 'ru',
  phone: '',
  customer_kind: 'person',
  created_at: '2026-03-12T00:00:00Z',
}

const MONTHS = Array.from({ length: 12 }, (_, index) => ({
  month: `2026-${String(index + 1).padStart(2, '0')}`,
  orders: 0,
}))

/** A customer with nothing measured yet — every optional figure absent. */
const FRESH = {
  profile: PROFILE,
  tier: {
    code: 'standard',
    discount_percent: '0',
    lifetime_spend: '0',
    steps: [
      { code: 'standard', from_spend: '0', discount_percent: '0', reached: true },
      { code: 'silver', from_spend: '100000', discount_percent: '4', reached: false },
      { code: 'gold', from_spend: '300000', discount_percent: '8', reached: false },
    ],
    next_code: 'silver',
    next_from_spend: '100000',
    to_next: '100000',
    progress_percent: '0.0',
  },
  lifetime: {
    orders: 0,
    in_progress: 0,
    spend: '0',
    average_order: null,
    saved: '0',
    average_days: null,
    on_time: 0,
    on_time_of: 0,
    months: MONTHS,
  },
}

/** The kit's own customer: 186 400 ₽ spent, Silver, 113 600 ₽ short of Gold. */
const SETTLED = {
  profile: PROFILE,
  tier: {
    code: 'silver',
    discount_percent: '4',
    lifetime_spend: '186400',
    steps: [
      { code: 'standard', from_spend: '0', discount_percent: '0', reached: true },
      { code: 'silver', from_spend: '100000', discount_percent: '4', reached: true },
      { code: 'gold', from_spend: '300000', discount_percent: '8', reached: false },
    ],
    next_code: 'gold',
    next_from_spend: '300000',
    to_next: '113600',
    progress_percent: '62.1',
  },
  lifetime: {
    orders: 14,
    in_progress: 2,
    spend: '186400',
    average_order: '13314.29',
    saved: '14820',
    average_days: '2.8',
    on_time: 13,
    on_time_of: 14,
    months: MONTHS,
  },
}

function answer(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  })
}

function serve(overview: unknown, extra: Record<string, unknown> = {}) {
  net.handler = (url: string) => {
    if (url.endsWith('/auth/me')) return answer(ACTOR)
    if (url.endsWith('/account')) return answer(overview)
    for (const [path, body] of Object.entries(extra)) {
      if (url.includes(path)) return answer(body)
    }
    return answer([], 404)
  }
}

function open(section = 'profile') {
  return render(
    <SessionProvider>
      <AccountPage
        locale="ru"
        section={section as never}
        onSection={() => {}}
        onCabinet={() => {}}
        onConfigure={() => {}}
      />
    </SessionProvider>,
  )
}

/** The four header plates, by their label. */
function plate(label: string): HTMLElement {
  const heading = screen.getByText(label)
  return heading.parentElement as HTMLElement
}

beforeEach(() => {
  net.handler = () => Promise.reject(new Error('no handler'))
})

describe('the header reports measurements', () => {
  it('shows an em dash, not a zero, for a customer with nothing dispatched', async () => {
    serve(FRESH)
    open()

    await waitFor(() => expect(screen.getByText('Заказов всего')).toBeTruthy())

    // Both figures on the lead-time plate are averages over dispatched orders,
    // and there are none: the mean lead time and the on-time count are absent.
    expect(within(plate('Средний срок')).getAllByText('—')).toHaveLength(2)
    // The average cheque is an average too, so it is absent for the same reason…
    expect(within(plate('Потрачено')).getByText('—')).toBeTruthy()
    // …but the total is not. Nought spent is a measurement, not a missing one,
    // and blanking it would hide the difference between the two.
    expect(within(plate('Потрачено')).getByText('0')).toBeTruthy()
    expect(within(plate('Заказов всего')).getAllByText('0')).toHaveLength(2)
  })

  it('counts orders and work in progress, which are counts and not averages', async () => {
    serve(SETTLED)
    open()

    await waitFor(() => expect(screen.getByText('Заказов всего')).toBeTruthy())

    expect(within(plate('Заказов всего')).getByText('14')).toBeTruthy()
    expect(within(plate('Заказов всего')).getByText('2')).toBeTruthy()
    expect(within(plate('Средний срок')).getByText('13 из 14')).toBeTruthy()
  })
})

describe('the loyalty ladder', () => {
  it('leads with the gap to the next tier, not the badge already held', async () => {
    serve(SETTLED)
    open()

    await waitFor(() => expect(screen.getByText(/ДО ТАРИФА GOLD/)).toBeTruthy())
    expect(screen.getByText(/113\s?600 ₽/)).toBeTruthy()
    expect(screen.getByText('Silver · −4%')).toBeTruthy()
  })

  it('lights the rungs reached and leaves the rest', async () => {
    serve(SETTLED)
    const { container } = open()

    await waitFor(() => expect(screen.getByText(/ДО ТАРИФА GOLD/)).toBeTruthy())

    const marks = [...container.querySelectorAll('.hv-tier__marks b')]
    expect(marks.map((mark) => mark.hasAttribute('data-on'))).toEqual([true, true, false])
    expect(container.querySelector<HTMLElement>('.hv-tier__fill')?.style.getPropertyValue('--p')).toBe(
      '62.1%',
    )
  })

  it('drops the gap and fills the bar at the top of the ladder', async () => {
    serve({
      ...SETTLED,
      tier: {
        ...SETTLED.tier,
        code: 'gold',
        discount_percent: '8',
        next_code: null,
        next_from_spend: null,
        to_next: null,
        progress_percent: null,
      },
    })
    const { container } = open()

    await waitFor(() => expect(screen.getByText('ВЫСШИЙ ТАРИФ ДОСТИГНУТ')).toBeTruthy())
    expect(screen.queryByText(/ДО ТАРИФА/)).toBeNull()
    expect(container.querySelector<HTMLElement>('.hv-tier__fill')?.style.getPropertyValue('--p')).toBe(
      '100%',
    )
  })
})

describe('the sections rail', () => {
  it('reports the section the caller asked for, not one of its own', async () => {
    serve(FRESH, { '/account/notifications': { on_paid: true, journal: false } })
    const chosen: string[] = []
    render(
      <SessionProvider>
        <AccountPage
          locale="ru"
          section="notify"
          onSection={(next) => chosen.push(next)}
          onCabinet={() => {}}
          onConfigure={() => {}}
        />
      </SessionProvider>,
    )

    await waitFor(() => expect(screen.getByText('Когда писать')).toBeTruthy())

    // The rail reports upwards rather than switching itself: the open section is
    // part of the address, and the shell owns the address.
    await userEvent.click(screen.getByRole('tab', { name: 'Безопасность' }))
    expect(chosen).toEqual(['sec'])
    expect(screen.getByText('Когда писать')).toBeTruthy()
  })
})
