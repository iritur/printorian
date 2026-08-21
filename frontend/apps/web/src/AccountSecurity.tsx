import { useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale } from '@printorian/ui'
import { api, translateError, useSession } from '@printorian/ui'

import { Setting, Switch } from './AccountBits'
import { NONE, device, lastSeen, shortDate } from './account'
import type { Notifications, SessionRow } from './account'

/**
 * «Безопасность» — the password, the live sessions, and the way out.
 *
 * Two-factor authentication is drawn and disabled. The kit has the row and the
 * farm has no TOTP: no secret storage, no enrolment, no recovery codes. A
 * switch that saved a preference nothing enforced would be worse than the empty
 * row — it would tell somebody their account had a second factor when it did
 * not — so it uses the kit's own idiom for a control that cannot move, the same
 * one «Задержка и начисление скидки» uses, and says why on the row.
 */
export function AccountSecurity({ locale }: { locale: Locale }) {
  const { signOut } = useSession()
  const [sessions, setSessions] = useState<SessionRow[] | null>(null)
  const [notify, setNotify] = useState<Notifications | null>(null)
  const [busy, setBusy] = useState(false)

  const load = async () => setSessions(await api.get<SessionRow[]>('/account/sessions'))

  useEffect(() => {
    void (async () => {
      await load().catch(() => setSessions([]))
    })()
    void api
      .get<Notifications>('/account/notifications')
      .then(setNotify)
      .catch(() => setNotify(null))
  }, [])

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await work()
      await load()
    } finally {
      setBusy(false)
    }
  }

  const others = (sessions ?? []).filter((row) => !row.is_current).length

  return (
    <>
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>Вход</span>
          <span className="hv-panel__aside">IDENTITY</span>
        </div>
        <div className="hv-panel__body--none">
          <PasswordRow locale={locale} />

          <Setting
            name="Двухфакторная аутентификация"
            hint="Пока недоступна: ферма не умеет выдавать и проверять одноразовые коды."
          >
            <Switch label="Двухфакторная аутентификация" checked={false} disabled onChange={() => {}} />
          </Setting>

          <Setting name="Уведомлять о новом входе">
            <Switch
              label="Уведомлять о новом входе"
              checked={notify?.on_new_sign_in ?? true}
              disabled={notify === null || busy}
              onChange={(next) => {
                setNotify(notify ? { ...notify, on_new_sign_in: next } : null)
                void api
                  .patch<Notifications>('/account/notifications', { on_new_sign_in: next })
                  .then(setNotify)
              }}
            />
          </Setting>
        </div>
      </section>

      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>Активные сеансы</span>
          <span className="hv-panel__aside">{sessions?.length ?? NONE}</span>
        </div>
        <div className="hv-panel__body--none">
          <div className="hv-table-wrap">
            <table className="hv-table">
              <thead>
                <tr>
                  <th>Устройство</th>
                  <th>Адрес</th>
                  <th>Последняя активность</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(sessions ?? []).map((row) => (
                  <tr key={row.id}>
                    <td>
                      {device(row.user_agent)}
                      {row.is_current && <div className="hv-micro">ТЕКУЩИЙ СЕАНС</div>}
                    </td>
                    <td className="hv-table__id">{row.client_ip || NONE}</td>
                    <td className={row.is_current ? 'hv-live' : undefined}>
                      {lastSeen(row.last_seen_at, locale)}
                    </td>
                    <td>
                      {row.is_current ? (
                        <span className="hv-micro">{NONE}</span>
                      ) : (
                        <button
                          className="hv-btn hv-btn--sm hv-btn--danger"
                          type="button"
                          disabled={busy}
                          onClick={() => void run(() => api.delete(`/account/sessions/${row.id}`))}
                        >
                          Завершить
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {sessions !== null && sessions.length === 0 && (
                  <tr>
                    <td colSpan={4} className="hv-faint">
                      Активных сеансов нет.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
        <div className="hv-panel__foot">
          <span>СЕАНС ЖИВЁТ 12 ЧАСОВ</span>
          <button
            className="hv-btn hv-btn--sm hv-btn--danger"
            type="button"
            disabled={busy || others === 0}
            onClick={() => void run(() => api.delete('/account/sessions'))}
          >
            Завершить все, кроме текущего
          </button>
        </div>
      </section>

      <DangerZone locale={locale} onClosed={() => void signOut()} />
    </>
  )
}

/** The password row: expands into the form rather than opening a dialog. */
function PasswordRow({ locale }: { locale: Locale }) {
  const { signOut } = useSession()
  const [open, setOpen] = useState(false)
  const [current, setCurrent] = useState('')
  const [replacement, setReplacement] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.post('/account/password', { current, replacement })
      /*
        Changing a password revokes every session, this one included — which is
        the point of doing it. The client has to be told, or it keeps rendering a
        signed-in screen whose every request now fails as unauthenticated.
      */
      await signOut()
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : 'Не удалось сменить пароль.',
      )
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <Setting name="Пароль" hint="Хранится как хэш Argon2id. Смена завершает все сеансы.">
        <button className="hv-btn hv-btn--sm" type="button" onClick={() => setOpen(true)}>
          Сменить
        </button>
      </Setting>
    )
  }

  return (
    <Setting
      name="Пароль"
      hint={error ?? 'Новый пароль — не короче десяти символов. После смены нужно войти заново.'}
      changed
    >
      <input
        className="hv-input"
        type="password"
        value={current}
        autoComplete="current-password"
        placeholder="Текущий"
        onChange={(event) => setCurrent(event.target.value)}
      />
      <input
        className="hv-input"
        type="password"
        value={replacement}
        autoComplete="new-password"
        placeholder="Новый"
        onChange={(event) => setReplacement(event.target.value)}
      />
      <button
        className="hv-btn hv-btn--sm hv-btn--primary"
        type="button"
        disabled={busy || !current || replacement.length < 10}
        onClick={() => void submit()}
      >
        Сохранить
      </button>
    </Setting>
  )
}

/**
 * «Данные и учётная запись» — the export, and closing the account.
 *
 * Closing is exactly what the kit's copy says and no more: sign-in stops, the
 * orders and documents stay. That is also the only legal version — the farm is
 * obliged to keep what it billed for.
 */
function DangerZone({ locale, onClosed }: { locale: Locale; onClosed: () => void }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const download = async () => {
    setBusy(true)
    try {
      const data = await api.get<unknown>('/account/export')
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
      )
      const link = document.createElement('a')
      link.href = url
      link.download = `printorian-${shortDate(new Date().toISOString(), locale)}.json`
      link.click()
      URL.revokeObjectURL(url)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="hv-frame hv-danger">
      <span className="hv-h hv-bad">Данные и учётная запись</span>
      <p
        className="hv-prose"
        style={{ fontSize: 'var(--hv-size-small)', margin: 'var(--hv-2) 0 var(--hv-3)' }}
      >
        Выгрузка содержит профиль, историю заказов и сметы. Удаление отключает вход; заказы и
        документы сохраняются — их обязывает хранить бухгалтерия.
      </p>
      <div className="hv-row">
        <button className="hv-btn" type="button" disabled={busy} onClick={() => void download()}>
          Выгрузить мои данные
        </button>
        {confirming ? (
          <>
            <span className="hv-micro hv-bad">ЭТО НЕЛЬЗЯ ОТМЕНИТЬ</span>
            <button
              className="hv-btn hv-btn--danger"
              type="button"
              disabled={busy}
              onClick={() => {
                setBusy(true)
                void api.post('/account/close').then(onClosed)
              }}
            >
              Подтвердить удаление
            </button>
            <button className="hv-btn" type="button" onClick={() => setConfirming(false)}>
              Отмена
            </button>
          </>
        ) : (
          <button className="hv-btn hv-btn--danger" type="button" onClick={() => setConfirming(true)}>
            Удалить учётную запись
          </button>
        )}
      </div>
    </section>
  )
}
