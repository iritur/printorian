import { useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale } from '@printorian/ui'
import { api, translateError } from '@printorian/ui'

import { Setting } from './AccountBits'
import type { Lifetime, Profile } from './account'

/**
 * «Профиль» — the personal-data form and the twelve-month activity chart.
 *
 * The form edits four fields and no more. Email is absent because changing a
 * login means proving the new address, which means mail the farm cannot yet
 * send; the panel says so on the row rather than offering a control that would
 * have to be refused.
 */

const KIND_LABEL: Record<string, string> = {
  person: 'Частное лицо',
  company: 'Юридическое лицо',
}

const MONTHS = ['ЯНВ', 'ФЕВ', 'МАР', 'АПР', 'МАЙ', 'ИЮН', 'ИЮЛ', 'АВГ', 'СЕН', 'ОКТ', 'НОЯ', 'ДЕК']

/** `2026-08` → `АВГ 2026`. Month names are the client's (ADR-0012). */
function monthLabel(key: string): string {
  const [year, month] = key.split('-')
  const index = Number(month) - 1
  return `${MONTHS[index] ?? month} ${year}`
}

export function AccountProfile({
  locale,
  profile,
  lifetime,
  onSaved,
}: {
  locale: Locale
  profile: Profile
  lifetime: Lifetime
  onSaved: (profile: Profile) => void
}) {
  const [draft, setDraft] = useState({
    display_name: profile.display_name,
    phone: profile.phone,
    locale: profile.locale,
    customer_kind: profile.customer_kind,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const dirty =
    draft.display_name !== profile.display_name ||
    draft.phone !== profile.phone ||
    draft.locale !== profile.locale ||
    draft.customer_kind !== profile.customer_kind

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      onSaved(await api.patch<Profile>('/account/profile', draft))
      setSaved(true)
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : 'Не удалось сохранить.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>Личные данные</span>
          <span className="hv-panel__aside">IDENTITY.USER</span>
        </div>
        <div className="hv-panel__body--none">
          <Setting
            name="Имя и фамилия"
            hint="Указывается в документах и на упаковке."
            changed={draft.display_name !== profile.display_name}
          >
            <input
              className="hv-input"
              value={draft.display_name}
              autoComplete="name"
              onChange={(event) => setDraft({ ...draft, display_name: event.target.value })}
            />
          </Setting>

          <Setting
            name="Электронная почта"
            hint="Логин и адрес для уведомлений. Смена адреса пока недоступна — она требует подтверждения письмом."
          >
            <span className="hv-micro hv-good">ПОДТВЕРЖДЕНА</span>
            <input className="hv-input" value={profile.email} readOnly disabled />
          </Setting>

          <Setting
            name="Телефон"
            hint="Только для курьера. СМС-рассылок нет."
            changed={draft.phone !== profile.phone}
          >
            <input
              className="hv-input"
              value={draft.phone}
              autoComplete="tel"
              placeholder="+7 900 000-00-00"
              onChange={(event) => setDraft({ ...draft, phone: event.target.value })}
            />
          </Setting>

          <Setting name="Язык интерфейса" changed={draft.locale !== profile.locale}>
            <select
              className="hv-select"
              value={draft.locale}
              onChange={(event) => setDraft({ ...draft, locale: event.target.value })}
            >
              <option value="ru">Русский</option>
              <option value="en">English</option>
            </select>
          </Setting>

          <Setting
            name="Тип заказчика"
            hint="Определяет доступные способы оплаты."
            changed={draft.customer_kind !== profile.customer_kind}
          >
            <select
              className="hv-select"
              value={draft.customer_kind}
              onChange={(event) =>
                setDraft({ ...draft, customer_kind: event.target.value as Profile['customer_kind'] })
              }
            >
              {Object.entries(KIND_LABEL).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </Setting>
        </div>
        <div className="hv-panel__foot">
          <span>
            {error ? (
              <span className="hv-bad">{error}</span>
            ) : saved && !dirty ? (
              <span className="hv-good">СОХРАНЕНО</span>
            ) : (
              'ИЗМЕНЕНИЯ ПРИМЕНЯЮТСЯ СРАЗУ'
            )}
          </span>
          <button
            className="hv-btn hv-btn--sm hv-btn--primary"
            type="button"
            disabled={!dirty || busy}
            onClick={() => void save()}
          >
            Сохранить
          </button>
        </div>
      </section>

      <Activity lifetime={lifetime} />
    </>
  )
}

/**
 * Twelve months of orders as one filled sparkline.
 *
 * Scaled to the busiest month rather than to a fixed ceiling, and the peak is
 * named beside the chart — without the label a sparkline says only "this shape",
 * and two customers with wildly different volumes would draw the same picture.
 *
 * A flat line at the bottom is the honest rendering of a customer who has not
 * ordered yet, so there is no empty state here: zero is a measurement.
 */
function Activity({ lifetime }: { lifetime: Lifetime }) {
  const months = lifetime.months
  const peak = months.reduce(
    (best, point) => (point.orders > best.orders ? point : best),
    months[0] ?? { month: '', orders: 0 },
  )
  const ceiling = Math.max(peak.orders, 1)
  const step = months.length > 1 ? 600 / (months.length - 1) : 600
  // 60 is the viewBox floor and 8 the headroom, so a peak month never touches
  // the top edge where its marker would be clipped.
  const points = months.map((point, index) => {
    const x = Math.round(index * step)
    const y = Math.round(60 - 8 - (point.orders / ceiling) * (60 - 16))
    return `${x},${y}`
  })

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>Активность</span>
        <span className="hv-panel__aside">12 МЕСЯЦЕВ</span>
      </div>
      <div className="hv-panel__body">
        <div className="hv-row hv-row--between" style={{ marginBottom: 'var(--hv-2)' }}>
          <span className="hv-label" style={{ margin: 0 }}>
            Заказы по месяцам
          </span>
          <span className="hv-micro">
            {peak.orders > 0
              ? `ПИК :: ${monthLabel(peak.month)} · ${peak.orders}`
              : 'ЗАКАЗОВ ПОКА НЕТ'}
          </span>
        </div>
        <svg
          className="hv-spark"
          viewBox="0 0 600 60"
          preserveAspectRatio="none"
          role="img"
          aria-label="Заказы по месяцам"
        >
          <polygon points={`0,60 ${points.join(' ')} 600,60`} />
          <polyline points={points.join(' ')} />
        </svg>
        <div className="hv-row hv-row--between" style={{ marginTop: 'var(--hv-2)' }}>
          <span className="hv-micro">{months[0] ? monthLabel(months[0].month) : ''}</span>
          <span className="hv-micro">
            {months.length > 0 ? monthLabel(months[months.length - 1]!.month) : ''}
          </span>
        </div>
      </div>
    </section>
  )
}
