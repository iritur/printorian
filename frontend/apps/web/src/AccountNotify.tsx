import { useEffect, useState } from 'react'

import { api } from '@printorian/ui'

import { Setting, Switch } from './AccountBits'
import type { Notifications } from './account'

/**
 * «Уведомления» — when the farm writes, and about what.
 *
 * One honest caveat, stated in the panel's own footer rather than hidden here:
 * **the farm has no mail gateway yet.** These preferences are stored, scoped to
 * the customer and read back, and nothing dispatches from them, because nothing
 * in this system sends email at all. Saying so on the screen is the only
 * version that is not a promise — and the settings are worth keeping now, so
 * that the day a sender lands it has something to obey rather than a default
 * applied to everybody at once.
 */

/** The rows, in the kit's order. `locked` is the one that cannot be turned off. */
const ROWS: {
  key: keyof Notifications
  name: string
  hint?: string
  locked?: boolean
}[] = [
  { key: 'on_paid', name: 'Заказ принят и оплачен' },
  {
    key: 'on_print_started',
    name: 'Печать началась',
    hint: 'С указанием машины и прогноза готовности.',
  },
  {
    key: 'on_every_stage',
    name: 'Каждая смена этапа',
    hint: 'Девять писем на заказ. По умолчанию выключено.',
  },
  {
    key: 'on_late_credit',
    name: 'Задержка и начисление скидки',
    hint: 'Отключить нельзя — это денежное уведомление.',
    locked: true,
  },
  { key: 'on_shipped', name: 'Заказ отправлен' },
  {
    key: 'journal',
    name: 'Новые отчёты журнала',
    hint: 'Один раз в неделю. Не связано с заказами.',
  },
]

export function AccountNotify({ email }: { email: string }) {
  const [settings, setSettings] = useState<Notifications | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void api
      .get<Notifications>('/account/notifications')
      .then(setSettings)
      .catch(() => setSettings(null))
  }, [])

  const flip = async (key: keyof Notifications, next: boolean) => {
    if (!settings) return
    setBusy(true)
    // Applied locally first: a switch that waits for a round trip before moving
    // reads as broken, and the server's answer replaces this a moment later.
    setSettings({ ...settings, [key]: next })
    try {
      setSettings(await api.patch<Notifications>('/account/notifications', { [key]: next }))
    } catch {
      setSettings(await api.get<Notifications>('/account/notifications'))
    } finally {
      setBusy(false)
    }
  }

  if (!settings) return <p className="hv-hint">Загрузка…</p>

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>Когда писать</span>
        <span className="hv-panel__aside">ТОЛЬКО ПО ВАШИМ ЗАКАЗАМ</span>
      </div>
      <div className="hv-panel__body--none">
        {ROWS.map((row) => (
          <Setting key={row.key} name={row.name} {...(row.hint ? { hint: row.hint } : {})}>
            <Switch
              label={row.name}
              checked={settings[row.key]}
              disabled={row.locked || busy}
              onChange={(next) => void flip(row.key, next)}
            />
          </Setting>
        ))}
      </div>
      <div className="hv-panel__foot">
        {/*
          Not «маркетинговых рассылок нет» alone, as the kit has it. That is true
          and so is this, and this is the one a customer would be misled by its
          absence: the switches are saved and nothing sends yet.
        */}
        <span>НАСТРОЙКИ СОХРАНЯЮТСЯ · ПОЧТОВЫЙ ШЛЮЗ ЕЩЁ НЕ ПОДКЛЮЧЁН</span>
        <span>АДРЕС :: {email.toUpperCase()}</span>
      </div>
    </section>
  )
}
