/**
 * The cell panel's drying column and the two dryer buttons.
 *
 * * **Never dried and lapsed are two different words**, because the backend sends
 *   them as two states and the first was never measured.
 * * **«Отправить на сушку» posts to `/dry`; «Высушена» posts to `/dried`**, and the
 *   second is offered for a spool in the dryer even when its state no longer says
 *   so — the way back must not disappear with the rule.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    posts: [] as string[],
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'POST') state.posts.push(String(input))
    return state.handler(String(input), init)
  }) as unknown as typeof fetch
  return state
})

import { CellDetail } from './CellDetail'

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

const cell = {
  id: 'c-1',
  address: 'D1-1',
  zone_code: 'D',
  capacity_lots: null,
  lot_count: 3,
  fill_percent: null,
  is_active: true,
}

function lot(id: string, label: string, drying: object, location_kind = 'stock') {
  return {
    id,
    label,
    family: 'PA',
    remaining_grams: '800.00',
    location_kind,
    cell: 'D1-1',
    shelf: null,
    received_at: '2026-09-01T09:00:00Z',
    drying,
  }
}

const detail = {
  cell,
  lots: [
    lot('lot-never', 'PA-001', { state: 'unknown', dried_at: null, valid_until: null, hours_left: null }),
    lot('lot-lapsed', 'PA-002', {
      state: 'expired',
      dried_at: '2026-09-01T09:00:00Z',
      valid_until: '2026-09-04T09:00:00Z',
      hours_left: null,
    }),
    lot(
      'lot-wet',
      'PA-003',
      { state: 'not_required', dried_at: null, valid_until: null, hours_left: null },
      'dryer',
    ),
  ],
  drying_valid_hours: 72,
  movements: [],
}

beforeEach(() => {
  net.posts.length = 0
  net.handler = (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return Promise.resolve(jsonOk({}))
    if (url.endsWith('/store/cells/D1-1')) return Promise.resolve(jsonOk(detail))
    return Promise.reject(new Error(`unexpected request: ${url}`))
  }
})

function row(label: string): HTMLElement {
  const found = screen.getByText(label).closest('tr')
  if (!found) throw new Error(`no row for ${label}`)
  return found
}

describe('the drying column', () => {
  it('keeps a spool never dried apart from one whose mark lapsed', async () => {
    render(
      <CellDetail
        address="D1-1"
        locale="ru"
        mayManage
        onClose={() => undefined}
        onChanged={() => Promise.resolve()}
      />,
    )
    await screen.findByText('PA-001')

    expect(row('PA-001').textContent).toContain('не сушилась')
    expect(row('PA-001').textContent).not.toContain('просрочена')
    expect(row('PA-002').textContent).toContain('просрочена')
    expect(screen.getByText(/ДЕЙСТВУЕТ 72 Ч/)).toBeInTheDocument()
  })

  it('sends a lapsed spool to the dryer and brings a drying one back', async () => {
    render(
      <CellDetail
        address="D1-1"
        locale="ru"
        mayManage
        onClose={() => undefined}
        onChanged={() => Promise.resolve()}
      />,
    )
    await screen.findByText('PA-002')

    await userEvent.click(within(row('PA-002')).getByRole('button', { name: 'Отправить на сушку' }))
    await waitFor(() =>
      expect(net.posts.some((url) => url.endsWith('/store/lots/lot-lapsed/dry'))).toBe(true),
    )

    // In the dryer with `not_required` — the rule was switched off after it went
    // in. The way back is still offered, or the spool is stuck there.
    expect(within(row('PA-003')).queryByRole('button', { name: 'Отправить на сушку' })).toBeNull()
    await userEvent.click(within(row('PA-003')).getByRole('button', { name: 'Высушена' }))
    await waitFor(() =>
      expect(net.posts.some((url) => url.endsWith('/store/lots/lot-wet/dried'))).toBe(true),
    )
  })
})
