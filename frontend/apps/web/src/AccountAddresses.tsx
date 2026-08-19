import { useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale } from '@printorian/ui'
import { api, translateError } from '@printorian/ui'

import type { Address } from './account'

/**
 * «Адреса доставки» — saved addresses, one of them the default.
 *
 * Addresses are *copied* into an order at checkout, never linked. Editing one
 * here therefore changes where the next parcel goes and nothing about where an
 * old one went, which is the only version that keeps «куда мы это отправили?»
 * answerable a year later.
 */

const EMPTY = {
  label: '',
  recipient: '',
  phone: '',
  postcode: '',
  city: '',
  address: '',
  note: '',
  is_default: false,
}

type Draft = typeof EMPTY

export function AccountAddresses({ locale }: { locale: Locale }) {
  const [rows, setRows] = useState<Address[] | null>(null)
  const [editing, setEditing] = useState<{ id: string | null; draft: Draft } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = async () => setRows(await api.get<Address[]>('/account/addresses'))

  useEffect(() => {
    void load().catch(() => setRows([]))
  }, [])

  const fail = (exc: unknown) =>
    setError(
      exc instanceof ApiError
        ? translateError(locale, { code: exc.code, details: exc.details })
        : 'Не удалось сохранить адрес.',
    )

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await work()
      await load()
      return true
    } catch (exc: unknown) {
      fail(exc)
      return false
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (!editing) return
    const { id, draft } = editing
    const ok = await run(() =>
      id === null
        ? api.post('/account/addresses', draft)
        : api.put(`/account/addresses/${id}`, draft),
    )
    if (ok) setEditing(null)
  }

  if (rows === null) return <p className="hv-hint">Загрузка…</p>

  return (
    <>
      <div className="hv-row hv-row--between">
        <span className="hv-micro">АДРЕС УТОЧНЯЕТ СТОИМОСТЬ ЛОГИСТИКИ В СМЕТЕ</span>
        <button
          className="hv-btn hv-btn--sm"
          type="button"
          disabled={busy}
          onClick={() => setEditing({ id: null, draft: { ...EMPTY } })}
        >
          Добавить адрес
        </button>
      </div>

      {error && <p className="hv-hint hv-bad">{error}</p>}

      {editing && (
        <AddressForm
          draft={editing.draft}
          busy={busy}
          onChange={(draft) => setEditing({ ...editing, draft })}
          onCancel={() => setEditing(null)}
          onSave={() => void save()}
        />
      )}

      {rows.map((row) => (
        <div key={row.id} className="hv-record" data-default={row.is_default ? 'true' : undefined}>
          <span className="hv-avatar hv-avatar--sm" style={{ fontSize: 11 }}>
            {(row.label.trim()[0] ?? row.city.trim()[0] ?? '·').toUpperCase()}
          </span>
          <span>
            <span className="hv-record__k">{row.label || row.city}</span>
            <span className="hv-record__v">
              {[row.postcode, row.city, row.address].filter(Boolean).join(', ')}
              {(row.recipient || row.phone) && (
                <>
                  <br />
                  {[row.recipient, row.phone].filter(Boolean).join(' · ')}
                </>
              )}
              {row.note && (
                <>
                  <br />
                  {row.note}
                </>
              )}
            </span>
          </span>
          <span className="hv-record__acts">
            {row.is_default ? (
              <span className="hv-record__badge">По умолчанию</span>
            ) : (
              <button
                className="hv-record__badge"
                type="button"
                disabled={busy}
                onClick={() => void run(() => api.post(`/account/addresses/${row.id}/default`))}
              >
                Сделать основным
              </button>
            )}
            <button
              className="hv-record__badge"
              type="button"
              disabled={busy}
              onClick={() =>
                setEditing({
                  id: row.id,
                  draft: {
                    label: row.label,
                    recipient: row.recipient,
                    phone: row.phone,
                    postcode: row.postcode,
                    city: row.city,
                    address: row.address,
                    note: row.note,
                    is_default: row.is_default,
                  },
                })
              }
            >
              Изменить
            </button>
            <button
              className="hv-record__badge"
              type="button"
              disabled={busy}
              onClick={() => void run(() => api.delete(`/account/addresses/${row.id}`))}
            >
              Удалить
            </button>
          </span>
        </div>
      ))}

      {/*
        Collection, which is a delivery method rather than an address — there is
        nowhere to send anything. It is here because the kit puts it here and the
        kit is right to: somebody comparing what a parcel costs wants the free
        option in the same list as the paid ones, not hidden a screen away in the
        checkout. Not editable, because it is the farm's address and not theirs.
      */}
      <div className="hv-record">
        <span className="hv-avatar hv-avatar--sm" style={{ fontSize: 11 }}>
          Ц
        </span>
        <span>
          <span className="hv-record__k">Самовывоз с фермы</span>
          <span className="hv-record__v">
            Выбирается в оформлении заказа
            <br />
            Доставка не начисляется
          </span>
        </span>
        <span className="hv-record__badge">Бесплатно</span>
      </div>

      {rows.length === 0 && !editing && (
        <p className="hv-hint">
          Сохранённых адресов нет. Без них заказ можно забрать с фермы или ввести адрес
          при оформлении.
        </p>
      )}
    </>
  )
}

/** The add/edit form, as a panel above the list it writes into. */
function AddressForm({
  draft,
  busy,
  onChange,
  onCancel,
  onSave,
}: {
  draft: Draft
  busy: boolean
  onChange: (draft: Draft) => void
  onCancel: () => void
  onSave: () => void
}) {
  const set = (field: keyof Draft) => (event: { target: { value: string } }) =>
    onChange({ ...draft, [field]: event.target.value })

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>Адрес</span>
        <span className="hv-panel__aside">ГОРОД И УЛИЦА ОБЯЗАТЕЛЬНЫ</span>
      </div>
      <div className="hv-panel__body hv-stack hv-stack--2">
        <div className="hv-grid hv-grid--2">
          <label className="hv-field">
            <span className="hv-label">Название</span>
            <input className="hv-input" value={draft.label} placeholder="Дом" onChange={set('label')} />
          </label>
          <label className="hv-field">
            <span className="hv-label">Индекс</span>
            <input className="hv-input" value={draft.postcode} onChange={set('postcode')} />
          </label>
          <label className="hv-field">
            <span className="hv-label">Город</span>
            <input className="hv-input" value={draft.city} required onChange={set('city')} />
          </label>
          <label className="hv-field">
            <span className="hv-label">Улица, дом, квартира</span>
            <input className="hv-input" value={draft.address} required onChange={set('address')} />
          </label>
          <label className="hv-field">
            <span className="hv-label">Получатель</span>
            <input
              className="hv-input"
              value={draft.recipient}
              autoComplete="name"
              onChange={set('recipient')}
            />
          </label>
          <label className="hv-field">
            <span className="hv-label">Телефон</span>
            <input
              className="hv-input"
              value={draft.phone}
              autoComplete="tel"
              onChange={set('phone')}
            />
          </label>
        </div>
        <label className="hv-field">
          <span className="hv-label">Как попасть</span>
          <input
            className="hv-input"
            value={draft.note}
            placeholder="Приём с 10:00 до 19:00 · пропуск по паспорту"
            onChange={set('note')}
          />
        </label>
      </div>
      <div className="hv-panel__foot">
        <button className="hv-btn hv-btn--sm" type="button" onClick={onCancel}>
          Отмена
        </button>
        <button
          className="hv-btn hv-btn--sm hv-btn--primary"
          type="button"
          disabled={busy || !draft.city.trim() || !draft.address.trim()}
          onClick={onSave}
        >
          Сохранить
        </button>
      </div>
    </section>
  )
}
