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

describe('the diagnostics section', () => {
  it('is in the rail even though the catalogue has no such section', async () => {
    // The server serves fourteen sections and the kit draws fifteen: diagnostics
    // is read-only, so there is nothing for a settings catalogue to carry. The
    // rail has to be one longer than what arrived, and the panel it opens reads
    // the health endpoints rather than any settings row.
    net.handler = (url: string) => {
      if (url.endsWith('/settings/sections')) return Promise.resolve(jsonOk(aSections()))
      if (url.endsWith('/settings/history')) return Promise.resolve(jsonOk([]))
      if (url.endsWith('/health/ready'))
        return Promise.resolve(jsonOk({ status: 'ok', checks: { database: 'ok' } }))
      if (url.endsWith('/health/workers'))
        return Promise.resolve(jsonOk({ status: 'ok', loops: {}, drivers: {} }))
      return Promise.reject(new Error('unexpected request: ' + url))
    }

    render(<SettingsPage locale="ru" />)
    await screen.findByText('Название фермы')

    // Four sections came back; the rail offers five, and the count beside it
    // says five rather than reporting the catalogue's length at the reader.
    expect(screen.getAllByRole('tab')).toHaveLength(aSections().length + 1)
    expect(screen.getByText(String(aSections().length + 1))).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: 'Диагностика' }))

    expect(await screen.findByText('База данных')).toBeInTheDocument()
    expect(screen.getByText('Проверки готовности')).toBeInTheDocument()
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

describe('the save bar', () => {
  it('cancel discards every pending edit at once', async () => {
    render(<SettingsPage locale="ru" />)

    const name = (await screen.findByLabelText('Название фермы')) as HTMLInputElement
    await userEvent.clear(name)
    await userEvent.type(name, 'KN-SOL.42')

    // A second edit in another section: the bar counts the whole screen, so
    // cancel has to forget both and not merely the tab in front of you.
    await userEvent.click(screen.getByRole('tab', { name: 'Скидки и тарифы' }))
    const discount = (await screen.findByLabelText('Скидка 1')) as HTMLInputElement
    await userEvent.clear(discount)
    await userEvent.type(discount, '9')

    expect(screen.getByText('ИЗМЕНЕНИЙ :: 2')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Отмена' }))

    expect(screen.getByText('ИЗМЕНЕНИЙ НЕТ — ВСЁ СОХРАНЕНО')).toBeInTheDocument()
    expect((screen.getByLabelText('Скидка 1') as HTMLInputElement).value).toBe('5')

    await userEvent.click(screen.getByRole('tab', { name: 'Общие' }))
    expect(((await screen.findByLabelText('Название фермы')) as HTMLInputElement).value).toBe(
      'KN-SOL.21',
    )
  })

  it('refuses an emptied number box rather than saving it as zero', async () => {
    // `Number('')` is 0, so a cleared rate used to save as free.
    const put: Array<[string, unknown]> = []
    serve((url, body) => {
      put.push([url, body])
      return {}
    })

    render(<SettingsPage locale="ru" />)
    await screen.findByLabelText('Название фермы')
    await userEvent.click(screen.getByRole('tab', { name: 'Обслуживание системы' }))

    const hour = (await screen.findByLabelText('Время запуска')) as HTMLInputElement
    await userEvent.clear(hour)
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Время запуска.*пустое/)
    expect(put).toEqual([])
  })
})

describe('the irreversible operations', () => {
  it('cannot be confirmed while the farm has no name', async () => {
    // The gate is "type the farm name". An empty name made an empty box match it,
    // so the confirm armed itself the moment the panel opened.
    const blank = aSections()
    blank[0]!.fields[0]!.value = ''
    net.handler = (url: string) => {
      if (url.endsWith('/settings/sections')) return Promise.resolve(jsonOk(blank))
      if (url.endsWith('/settings/history')) return Promise.resolve(jsonOk([]))
      return Promise.reject(new Error('unexpected request: ' + url))
    }

    render(<SettingsPage locale="ru" />)
    await screen.findByLabelText('Название фермы')
    await userEvent.click(screen.getByRole('tab', { name: 'Обслуживание системы' }))

    await screen.findByText('Необратимые операции')
    await userEvent.click(screen.getAllByRole('button', { name: 'Сбросить' })[0]!)

    expect(screen.queryByRole('button', { name: 'Подтвердить' })).not.toBeInTheDocument()
    expect(
      screen.getByText('Сначала задайте название фермы — им подтверждается операция'),
    ).toBeInTheDocument()
  })
})

describe('the panels', () => {
  it('draws one panel per run of fields sharing a group', async () => {
    // What the screen guarantees on its own. That a group is never *split* in
    // the first place is the server's guarantee, and is tested there —
    // `test_settings_catalogue.py::test_no_section_repeats_a_group_heading`.
    // React tolerates the duplicate sibling keys the split used to produce, so
    // there is no assertion here that would have caught them; the positional
    // key in the component is a guard, not something this test proves.
    const repeated = [
      {
        id: 'general',
        fields: (['a', 'b', 'a'] as const).map((suffix, index) => ({
          key: `general.farm_name_${index}`,
          section: 'general',
          kind: 'string',
          value: `v${index}`,
          default: '',
          is_overridden: false,
          is_set: false,
          options: [],
          group: suffix === 'a' ? 'general.farm' : 'general.display',
        })),
      },
    ]
    net.handler = (url: string) => {
      if (url.endsWith('/settings/sections')) return Promise.resolve(jsonOk(repeated))
      if (url.endsWith('/settings/history')) return Promise.resolve(jsonOk([]))
      return Promise.reject(new Error('unexpected request: ' + url))
    }

    render(<SettingsPage locale="ru" />)

    // Three fields in three runs — each keeps its own value rather than two of
    // them being reconciled into one another's DOM.
    for (const index of [0, 1, 2]) {
      expect(await screen.findByText(`general.farm_name_${index}`)).toBeInTheDocument()
    }
    expect(screen.getAllByText('Ферма')).toHaveLength(2)
  })
})
