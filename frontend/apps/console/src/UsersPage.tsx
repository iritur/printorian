import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Column, Locale, MessageKey, StatusTag } from '@printorian/ui'
import { DataTable, api, translate, translateError, useChrome, useSession } from '@printorian/ui'

import { Field } from './FleetAdmin'

/**
 * Staff administration — owner only (`manage_users`).
 *
 * This is the screen that makes roles real: it is where an owner turns a person
 * into an operator, an engineer, or a manager. Everything it offers is enforced
 * by the API too; the permission check here only decides what to draw.
 */

const MANAGE_USERS = 'manage_users'
const ROLES = ['customer', 'operator', 'engineer', 'manager', 'owner'] as const

type RoleName = (typeof ROLES)[number]

interface User {
  id: string
  email: string
  display_name: string
  role: RoleName
  is_active: boolean
  created_at: string
}

export function UsersPage({ locale }: { locale: Locale }) {
  const { actor, ready } = useSession()
  const t = useCallback((key: MessageKey) => translate(locale, key), [locale])

  const [users, setUsers] = useState<User[] | null>(null)

  /*
    The kit's chrome here says «SESSIONS :: 6 АКТИВНЫХ», which this screen cannot
    count: sessions are listed per user and only for the caller's own account.
    Accounts and how many of them are active is what it does know.
  */
  useChrome(
    users
      ? {
          meta: [
            { label: 'IDENTITY.USERS', value: String(users.length) },
            { label: 'АКТИВНЫХ', value: String(users.filter((row) => row.is_active).length) },
          ],
        }
      : null,
  )
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const entitled = actor?.permissions.includes(MANAGE_USERS) ?? false

  const refetch = useCallback(async () => {
    try {
      setUsers(await api.get<User[]>('/users'))
      setError(null)
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
    }
  }, [locale])

  useEffect(() => {
    if (entitled) void refetch()
  }, [entitled, refetch])

  const mutate = useCallback(
    async (work: () => Promise<unknown>) => {
      try {
        await work()
        setError(null)
      } catch (exc: unknown) {
        // Surfaced rather than swallowed: the interesting failures here are the
        // self-lockout guards, and a silently ignored click looks like a bug.
        setError(
          exc instanceof ApiError
            ? translateError(locale, { code: exc.code, details: exc.details })
            : translate(locale, 'error.internal'),
        )
      } finally {
        await refetch()
      }
    },
    [locale, refetch],
  )

  const columns = useMemo<Column<User>[]>(
    () => [
      {
        key: 'email',
        header: t('users.email'),
        value: (row) => row.email,
        render: (row) => (
          <span className="users__email">
            {row.email}
            {row.id === actor?.user_id && (
              <small className="admin-detail__muted"> · {t('users.self_hint')}</small>
            )}
          </span>
        ),
      },
      { key: 'name', header: t('users.name'), value: (row) => row.display_name },
      {
        key: 'role',
        header: t('users.role'),
        value: (row) => row.role,
        render: (row) => {
          // Your own role is shown, not offered: changing it is refused by the
          // API, so a live dropdown here would be a control that cannot work.
          if (row.id === actor?.user_id) return translate(locale, `role.${row.role}` as MessageKey)
          return (
            <select
              value={row.role}
              aria-label={`${t('users.role')} — ${row.email}`}
              onChange={(event) =>
                void mutate(() =>
                  api.put(`/users/${row.id}/role?role=${event.target.value}`, undefined),
                )
              }
            >
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {translate(locale, `role.${role}` as MessageKey)}
                </option>
              ))}
            </select>
          )
        },
      },
      {
        key: 'active',
        header: t('users.active'),
        value: (row) => row.is_active,
        render: (row) =>
          row.id === actor?.user_id ? (
            '—'
          ) : (
            <button
              type="button"
              onClick={() =>
                void mutate(() =>
                  api.put(`/users/${row.id}/active?is_active=${!row.is_active}`, undefined),
                )
              }
            >
              {t(row.is_active ? 'users.deactivate' : 'users.activate')}
            </button>
          ),
      },
      {
        key: 'created_at',
        header: t('users.created'),
        value: (row) => new Date(row.created_at),
        render: (row) => new Date(row.created_at).toLocaleDateString(locale),
      },
    ],
    [actor, locale, mutate, t],
  )

  const tags = useMemo<StatusTag<User>[]>(
    () =>
      ROLES.map((role) => ({
        key: role,
        label: translate(locale, `role.${role}` as MessageKey),
        match: (row: User) => row.role === role,
        tone: role === 'owner' ? ('good' as const) : ('neutral' as const),
      })),
    [locale],
  )

  if (ready && !entitled) return <p className="notice">{t('fleet.forbidden')}</p>

  return (
    <section className="users">
      <header className="fleet__header">
        <h2>{t('users.title')}</h2>
        {!adding && (
          <button type="button" onClick={() => setAdding(true)}>
            {t('users.add')}
          </button>
        )}
      </header>

      {adding && (
        <StaffForm
          locale={locale}
          onCancel={() => setAdding(false)}
          onDone={() => {
            setAdding(false)
            void refetch()
          }}
        />
      )}

      {error && <p className="cfg__error">{error}</p>}

      <DataTable
        rows={users ?? []}
        columns={columns}
        rowKey={(row) => row.id}
        statusTags={tags}
        caption={t('users.title')}
        emptyLabel={t('common.empty')}
        isLoading={users === null}
        loadingLabel={t('common.loading')}
        initialSort={{ key: 'email', direction: 'asc' }}
      />
    </section>
  )
}

function StaffForm({
  locale,
  onDone,
  onCancel,
}: {
  locale: Locale
  onDone: () => void
  onCancel: () => void
}) {
  const t = (key: MessageKey) => translate(locale, key)
  const [form, setForm] = useState({
    email: '',
    display_name: '',
    password: '',
    role: 'operator' as RoleName,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/users', form)
      onDone()
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="admin-form" onSubmit={(event) => void submit(event)}>
      <h3>{t('users.add')}</h3>

      <div className="admin-form__grid">
        <Field label={t('users.email')}>
          <input
            type="email"
            required
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
          />
        </Field>
        <Field label={t('users.name')}>
          <input
            required
            value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })}
          />
        </Field>
        <Field label={t('checkout.password')}>
          <input
            type="password"
            autoComplete="new-password"
            required
            // Matches the server's rule, so a rejection happens in the field
            // rather than after a round-trip.
            minLength={10}
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
          />
        </Field>
        <Field label={t('users.role')}>
          <select
            value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value as RoleName })}
          >
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {translate(locale, `role.${role}` as MessageKey)}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {error && <p className="cfg__error">{error}</p>}

      <div className="admin-form__actions">
        <button type="submit" disabled={busy}>
          {t('common.save')}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>
          {t('common.cancel')}
        </button>
      </div>
    </form>
  )
}
