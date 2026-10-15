/**
 * The non-filament half of the store (#35), on what a glance cannot check.
 *
 * * An item nobody has put anywhere says «не размещена» — not an empty cell.
 * * Issuing sends the quantity and the basis the person typed, and only a
 *   manager is offered the controls at all.
 * * The movements feed interleaves the two ledgers by time and says each
 *   quantity in its own unit — grams for a spool, pieces for a box.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    sent: [] as { url: string; body: unknown }[],
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    state.handler(String(input), init)) as unknown as typeof fetch
  return state
})

import type { Article, ArticleMovement } from './Articles'
import { ArticlesPanel } from './Articles'
import type { Movement } from './CellDetail'
import { MovementFeed } from './MovementFeed'

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

const nozzle: Article = {
  id: 'a1',
  kind: 'spare_part',
  code: 'NZ-04',
  name: 'Сопло 0.4',
  unit: 'piece',
  on_hand: '6.00',
  reorder_at: '0.00',
  cell: null,
  is_active: true,
}

beforeEach(() => {
  net.sent = []
  net.handler = (url: string, init?: RequestInit) => {
    net.sent.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null })
    return Promise.resolve(jsonOk(nozzle))
  }
})

describe('the articles panel', () => {
  it('says an item nobody has placed is not placed, and its count in its unit', () => {
    render(<ArticlesPanel articles={[nozzle]} locale="ru" mayManage={false} onChanged={vi.fn()} />)

    expect(screen.getByText('не размещена')).toBeInTheDocument()
    expect(screen.getByText('6 шт')).toBeInTheDocument()
    // The floor reads; it is offered nothing to change.
    expect(screen.queryByRole('button', { name: 'Выдача' })).toBeNull()
  })

  it('issues the quantity with the basis the manager typed', async () => {
    const onChanged = vi.fn(() => Promise.resolve())
    render(<ArticlesPanel articles={[nozzle]} locale="ru" mayManage={true} onChanged={onChanged} />)

    fireEvent.change(screen.getByLabelText('Кол-во'), { target: { value: '1' } })
    fireEvent.change(screen.getByLabelText('Основание'), { target: { value: 'SV-0407' } })
    fireEvent.click(screen.getByRole('button', { name: 'Выдача' }))

    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(net.sent).toEqual([
      { url: '/api/store/articles/a1/issue', body: { quantity: '1', reference: 'SV-0407' } },
    ])
  })
})

describe('the movements feed', () => {
  it('interleaves spools and articles by time, each in its own unit', () => {
    const spool: Movement = {
      id: 'm1',
      lot_id: 'l1',
      sequence: 2,
      reason: 'stock.written_off',
      grams: '28.00',
      remaining_after: '972.00',
      at: '2026-10-15T09:05:00Z',
      actor_id: null,
      from_kind: 'stock',
      from_address: 'A1-2',
      to_kind: 'consumed',
      to_address: null,
      note: 'Хвост катушки',
    }
    const box: ArticleMovement = {
      id: 'm2',
      article_id: 'a2',
      kind: 'packaging',
      code: 'box-a',
      name: 'Коробка A',
      unit: 'piece',
      reason: 'stock.issued',
      quantity: '6.00',
      on_hand_after: '204.00',
      at: '2026-10-15T17:10:00Z',
      from_address: 'C6-1',
      to_address: null,
      reference: 'PK-2147',
      note: null,
    }

    render(<MovementFeed spools={[spool]} articles={[box]} locale="ru" />)

    const rows = screen.getAllByRole('row').slice(1)
    // Newest first: the parcel's box at 17:10 above the spool's tail at 09:05.
    expect(rows[0]).toHaveTextContent('box-a Коробка A')
    expect(rows[0]).toHaveTextContent('6 шт')
    expect(rows[0]).toHaveTextContent('PK-2147')
    expect(rows[1]).toHaveTextContent('28 г')
    expect(rows[1]).toHaveTextContent('Хвост катушки')
  })
})
