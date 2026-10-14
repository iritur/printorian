/**
 * «Ключи API» at the screen.
 *
 * * **The key appears once**, in the box shown after creation, and is gone
 *   from the page when that box is dismissed — the list carries the prefix only.
 * * **A never-used key says so** rather than drawing a dash.
 * * **Revoking posts to that key's route.**
 * * **Nothing is requested or drawn without `manage_users`.**
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    calls: [] as string[],
    posts: [] as { url: string; body: string }[],
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    state.calls.push(String(input))
    if (init?.method === 'POST') state.posts.push({ url: String(input), body: String(init.body) })
    return state.handler(String(input), init)
  }) as unknown as typeof fetch
  return state
})

import type * as UiModule from '@printorian/ui'

import { ApiKeysPanel } from './ApiKeysPanel'

const session = vi.hoisted(() => ({ permissions: ['manage_users', 'manage_settings'] }))

vi.mock('@printorian/ui', async () => {
  const actual = await vi.importActual<typeof UiModule>('@printorian/ui')
  return {
    ...actual,
    useSession: () => ({ ready: true, actor: { user_id: 'u1', permissions: session.permissions } }),
  }
})

function jsonOk(body: unknown, status = 200): Response {
  return { ok: true, status, json: () => Promise.resolve(body) } as unknown as Response
}

const SECRET = 'pk_live_8f41Abcdefghijklmnopqrstuvwxyz0123456789-_'

const floor = {
  id: 'k1',
  name: 'Экран цеха',
  prefix: 'pk_live_8f41',
  permissions: ['view_production'],
  created_at: '2026-03-12T09:00:00Z',
  last_used_at: null,
  revoked_at: null,
}

beforeEach(() => {
  session.permissions = ['manage_users', 'manage_settings']
  net.calls.length = 0
  net.posts.length = 0
  net.handler = (url: string, init?: RequestInit) => {
    if (init?.method === 'POST' && url.endsWith('/api-keys'))
      return Promise.resolve(jsonOk({ key: floor, secret: SECRET }, 201))
    if (init?.method === 'POST') return Promise.resolve(jsonOk(floor))
    if (url.endsWith('/api-keys/permissions'))
      return Promise.resolve(jsonOk(['view_all_orders', 'view_production']))
    if (url.endsWith('/api-keys')) return Promise.resolve(jsonOk([floor]))
    return Promise.reject(new Error(`unexpected request: ${url}`))
  }
})

describe('the API keys panel', () => {
  it('lists the prefix, says a never-used key was never used, and revokes by id', async () => {
    render(<ApiKeysPanel locale="ru" />)

    const row = (await screen.findByText('Экран цеха')).closest('tr') as HTMLElement
    expect(row.textContent).toContain('pk_live_8f41…')
    expect(row.textContent).toContain('не использовался')

    await userEvent.click(within(row).getByRole('button', { name: 'Отозвать' }))
    await waitFor(() =>
      expect(net.posts.some((post) => post.url.endsWith('/api-keys/k1/revoke'))).toBe(true),
    )
  })

  it('shows the new key once, and it is gone when the box is dismissed', async () => {
    render(<ApiKeysPanel locale="ru" />)
    await screen.findByText('Экран цеха')

    await userEvent.type(screen.getByRole('textbox', { name: 'Название' }), 'Выгрузка в 1С')
    await userEvent.click(screen.getByRole('checkbox', { name: /view_all_orders/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Создать ключ' }))

    expect(await screen.findByText(SECRET)).toBeInTheDocument()
    const sent = net.posts.find((post) => post.url.endsWith('/api-keys'))
    expect(JSON.parse(sent?.body ?? '{}')).toEqual({
      name: 'Выгрузка в 1С',
      permissions: ['view_all_orders'],
    })

    await userEvent.click(screen.getByRole('button', { name: 'Готово' }))
    expect(document.body.textContent ?? '').not.toContain(SECRET)
  })

  it('requests and draws nothing for somebody who may not manage users', () => {
    session.permissions = ['manage_settings']
    const { container } = render(<ApiKeysPanel locale="ru" />)
    expect(container.textContent).toBe('')
    expect(net.calls).toEqual([])
  })
})
