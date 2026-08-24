/**
 * The settings screen, on the claims a glance cannot check.
 */

import { render, screen, waitFor } from '@testing-library/react'
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

import { SettingsPage } from './SettingsPage'

const session = vi.hoisted(() => ({ permissions: ['manage_settings'] }))

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

interface Field {
  key: string
  section: string
  kind: string
  value: unknown
  default: unknown
  is_overridden: boolean
  is_set: boolean
  options: string[]
  group?: string | null
}

function aSections(): { id: string; fields: Field[] }[] {
  return [
    {
      id: 'general',
      fields: [
        {
          key: 'general.farm_name',
          section: 'general',
          kind: 'string',
          value: 'KN-SOL.21',
          default: 'KN-SOL.21',
          is_overridden: false,
          is_set: false,
          options: [],
          group: 'general.farm',
        },
      ],
    },
    {
      id: 'discounts',
      fields: [
        {
          key: 'pricing.discounts',
          section: 'discounts',
          kind: 'table',
          value: [{ min_quantity: 10, percent: '5' }],
          default: [],
          is_overridden: true,
          is_set: false,
          options: [],
        },
        {
          key: 'pricing.tiers',
          section: 'discounts',
          kind: 'table',
          value: [
            { code: 'standard', discount_percent: '0', margin_percent_override: null },
            { code: 'silver', discount_percent: '4', margin_percent_override: null },
          ],
          default: [],
          is_overridden: true,
          is_set: false,
          options: [],
        },
      ],
    },
    {
      id: 'finance',
      fields: [
        {
          key: 'finance.yookassa_secret_key',
          section: 'finance',
          kind: 'secret',
          value: null,
          default: null,
          is_overridden: true,
          is_set: true,
          options: [],
        },
      ],
    },
    {
      id: 'maintenance',
      fields: [
        {
          key: 'maintenance.backup_hour',
          section: 'maintenance',
          kind: 'integer',
          value: 3,
          default: 3,
          is_overridden: false,
          is_set: false,
          options: [],
        },
      ],
    },
  ]
}

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

function serve(onPut?: (url: string, body: unknown) => unknown) {
  net.handler = (url: string, init?: RequestInit) => {
    if (url.endsWith('/settings/sections')) return Promise.resolve(jsonOk(aSections()))
    if (url.endsWith('/settings/history')) return Promise.resolve(jsonOk([]))
    if (init?.method === 'PUT') {
      const body = JSON.parse(String(init.body))
      return Promise.resolve(jsonOk(onPut?.(url, body) ?? {}))
    }
    return Promise.reject(new Error('unexpected request: ' + url))
  }
}

beforeEach(() => {
  session.permissions = ['manage_settings']
  serve()
})

describe('the sections', () => {
  it('lists the rail headings and draws the field rows', async () => {
    render(<SettingsPage locale="ru" />)

    expect(await screen.findByText('Название фермы')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Общие' })).toBeInTheDocument()
    expect(screen.getByText('Ферма')).toBeInTheDocument()
    expect(screen.getAllByRole('tab').length).toBeGreaterThan(1)
  })

  it('never shows a saved secret back', async () => {
    render(<SettingsPage locale="ru" />)

    await screen.findByText('Название фермы')
    await userEvent.click(screen.getByRole('tab', { name: 'Финансы' }))

    expect(await screen.findByText('КЛЮЧ СОХРАНЁН')).toBeInTheDocument()
    expect(screen.queryByText(/sk_/)).not.toBeInTheDocument()
  })

  it('edits the volume ladder as a table of rungs', async () => {
    render(<SettingsPage locale="ru" />)
    await screen.findByText('Название фермы')
    await userEvent.click(screen.getByRole('tab', { name: 'Скидки и тарифы' }))

    expect(await screen.findByText('Лестница за объём')).toBeInTheDocument()
    expect(screen.getByDisplayValue('10')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Добавить ступень' }))

    expect(screen.getByText('ИЗМЕНЕНИЙ :: 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Вернуть' })).toBeInTheDocument()
  })

  it('edits a customer tier discount', async () => {
    render(<SettingsPage locale="ru" />)
    await screen.findByText('Название фермы')
    await userEvent.click(screen.getByRole('tab', { name: 'Скидки и тарифы' }))

    expect(await screen.findByText('Тарифы клиентов')).toBeInTheDocument()
    const input = screen.getByLabelText('Скидка silver')
    await userEvent.clear(input)
    await userEvent.type(input, '7')

    expect(screen.getByText('ИЗМЕНЕНИЙ :: 1')).toBeInTheDocument()
  })

  it('resets rates only after the farm name is typed', async () => {
    const posted: string[] = []
    net.handler = (url: string, init?: RequestInit) => {
      if (url.endsWith('/settings/sections')) return Promise.resolve(jsonOk(aSections()))
      if (url.endsWith('/settings/history')) return Promise.resolve(jsonOk([]))
      if (url.endsWith('/settings/reset-rates') && init?.method === 'POST') {
        posted.push(url)
        return Promise.resolve(jsonOk({ reset: 1 }))
      }
      return Promise.reject(new Error('unexpected request: ' + url))
    }

    render(<SettingsPage locale="ru" />)
    await screen.findByText('Название фермы')
    await userEvent.click(screen.getByRole('tab', { name: 'Обслуживание системы' }))

    await screen.findByText('Необратимые операции')
    await userEvent.click(screen.getByRole('button', { name: 'Сбросить' }))

    const confirm = screen.getByRole('button', { name: 'Подтвердить' })
    expect(confirm).toBeDisabled()

    await userEvent.type(
      screen.getByLabelText('Введите название фермы для подтверждения'),
      'KN-SOL.21',
    )
    expect(confirm).not.toBeDisabled()

    await userEvent.click(confirm)
    await waitFor(() => expect(posted.length).toBe(1))
  })

  it('shows the change log under system maintenance', async () => {
    net.handler = (url: string) => {
      if (url.endsWith('/settings/sections')) return Promise.resolve(jsonOk(aSections()))
      if (url.endsWith('/settings/history'))
        return Promise.resolve(
          jsonOk([
            {
              key: 'pricing.margin_percent',
              old_value: '28',
              new_value: '30',
              changed_at: '2026-08-08T11:42:00Z',
              changed_by_name: 'boss@example.com',
            },
          ]),
        )
      return Promise.reject(new Error('unexpected request: ' + url))
    }

    render(<SettingsPage locale="ru" />)
    await screen.findByText('Название фермы')
    await userEvent.click(screen.getByRole('tab', { name: 'Обслуживание системы' }))

    expect(await screen.findByText('Журнал изменений настроек')).toBeInTheDocument()
    expect(screen.getByText('pricing.margin_percent')).toBeInTheDocument()
    expect(screen.getByText('boss@example.com')).toBeInTheDocument()
    expect(screen.getByText('28')).toBeInTheDocument()
    expect(screen.getByText('30')).toBeInTheDocument()
  })
})

describe('editing', () => {
  it('marks a row dirty, counts it, and PUTs exactly that key on save', async () => {
    const put: Array<[string, unknown]> = []
    serve((url, body) => {
      put.push([url, body])
      return {}
    })

    render(<SettingsPage locale="ru" />)

    const input = await screen.findByLabelText('Название фермы')
    await userEvent.clear(input)
    await userEvent.type(input, 'KN-SOL.42')

    expect(screen.getByText('ИЗМЕНЕНИЙ :: 1')).toBeInTheDocument()
    expect(screen.getByText(/БЫЛО KN-SOL.21/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    await waitFor(() => expect(put.length).toBe(1))
    // The base URL is the dev proxy's (`/api`), so match the tail, not the whole.
    expect(put[0]?.[0]?.endsWith('/settings/general.farm_name')).toBe(true)
    expect(put[0]?.[1]).toEqual({ value: 'KN-SOL.42' })
  })

  it('revert returns the row to the committed value and clears the bar', async () => {
    render(<SettingsPage locale="ru" />)

    const input = (await screen.findByLabelText('Название фермы')) as HTMLInputElement
    await userEvent.clear(input)
    await userEvent.type(input, 'KN-SOL.42')

    expect(screen.getByText('ИЗМЕНЕНИЙ :: 1')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Вернуть' }))

    expect(screen.getByText('ИЗМЕНЕНИЙ НЕТ — ВСЁ СОХРАНЕНО')).toBeInTheDocument()
    const reverted = (await screen.findByLabelText('Название фермы')) as HTMLInputElement
    expect(reverted.value).toBe('KN-SOL.21')
  })
})
