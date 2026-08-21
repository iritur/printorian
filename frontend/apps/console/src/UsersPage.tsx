import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Column, Locale, MessageKey, StatusTag } from '@printorian/ui'
import {
  DataTable,
  Modal,
  api,
  translate,
  translateError,
  useChrome,
  useSession,
} from '@printorian/ui'

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
  /*
    Held by id, not by value. A captured row would go stale the moment the popup
    saved anything — the same trap the materials detail documents — so the open
    account is looked up again out of whatever the last fetch returned.
  */
  const [editing, setEditing] = useState<User | null>(null)
  const open = editing ? (users?.find((row) => row.id === editing.id) ?? editing) : null

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
    if (!entitled) return
    void (async () => {
      await refetch()
    })()
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
      /*
        Both of these used to be live controls sitting in the cells: a `<select>`
        that fired a PUT on every change, and a button that flipped an account's
        access. In a sortable table that is a bad place for either — the row you
        are pointing at is one re-sort away from being a different person, and
        neither action announced whose account it had just changed.

        They read as facts here and are edited in the popup, where the person is
        named in the title and the change is pressed deliberately.
      */
      {
        key: 'role',
        header: t('users.role'),
        value: (row) => row.role,
        render: (row) => translate(locale, `role.${row.role}` as MessageKey),
      },
      {
        key: 'active',
        header: t('users.active'),
        value: (row) => row.is_active,
        render: (row) => (
          <span className="hv-state" data-state={row.is_active ? 'idle' : 'paused'}>
            {t(row.is_active ? 'users.state_active' : 'users.state_inactive')}
          </span>
        ),
      },
      {
        key: 'created_at',
        header: t('users.created'),
        value: (row) => new Date(row.created_at),
        render: (row) => new Date(row.created_at).toLocaleDateString(locale),
      },
    ],
    // `mutate` is gone from here with the controls that used it: the columns
    // render facts now, and nothing in them writes.
    [actor, locale, t],
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
        {/* Stays mounted *and* focusable while the popup is open. It used to
            unmount, which left the modal's focus-restore with nothing to return
            to; disabling it instead was the same bug in a different costume,
            because a disabled control cannot take focus either. `aria-expanded`
            carries the state, and the backdrop is what stops it being clicked. */}
        <button
          className="hv-btn"
          type="button"
          onClick={() => setAdding(true)}
          aria-expanded={adding}
        >
          {t('users.add')}
        </button>
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

      {error && (
        <p className="hv-hint hv-bad" role="alert">
          {error}
        </p>
      )}

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
        onRowActivate={(row) => setEditing(row)}
      />

      {/* Re-read from the freshly fetched list rather than held by value, so the
          popup follows the server: after a role change the window shows what was
          actually saved, not what the client sent. */}
      {open && (
        <StaffDetail
          user={open}
          locale={locale}
          isSelf={open.id === actor?.user_id}
          onRun={mutate}
          onClose={() => setEditing(null)}
        />
      )}
    </section>
  )
}

/**
 * One staff account, open.
 *
 * The role is a draft until «Сохранить» — a dropdown that saved on change was
 * how somebody's access could be altered by a stray scroll wheel. Access itself
 * is its own footer button rather than part of the save, because switching an
 * account off is not an edit to a field: it takes effect at once and reads as a
 * decision, the same shape the journal gives «Удалить».
 *
 * Your own account shows both and offers neither. The API refuses a self-role
 * change and a self-deactivation, so a live control here would be a button that
 * cannot work — and finding that out by pressing it is not a design.
 */
function StaffDetail({
  user,
  locale,
  isSelf,
  onRun,
  onClose,
}: {
  user: User
  locale: Locale
  isSelf: boolean
  onRun: (work: () => Promise<unknown>) => Promise<void>
  onClose: () => void
}) {
  const t = (key: MessageKey) => translate(locale, key)
  /*
    The edit in progress, remembered together with the server value it was made
    against. That pairing *is* the re-sync: the moment a save lands, `user.role`
    changes, `against` no longer matches, and the selector falls back to what the
    server now says — so the button stops offering a save it has already made.
    An effect did this before, one render later, and it is the shape
    `react-hooks/set-state-in-effect` exists to discourage.
  */
  const [draft, setDraft] = useState<{ against: RoleName; value: RoleName } | null>(null)
  const role = draft?.against === user.role ? draft.value : user.role
  const setRole = (value: RoleName) => setDraft({ against: user.role, value })
  const [busy, setBusy] = useState(false)

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await onRun(work)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`${t('users.detail')} :: ${user.display_name || user.email}`}
      meta={[
        { label: 'EMAIL', value: user.email },
        { label: 'РОЛЬ', value: translate(locale, `role.${user.role}` as MessageKey) },
      ]}
      status={t(user.is_active ? 'users.state_active' : 'users.state_inactive')}
      path={`/IDENTITY/USERS.DB/${user.email.split('@')[0]?.toUpperCase() ?? ''}`}
      onClose={onClose}
      footer={
        <>
          <span className="hv-row">
            {isSelf ? (
              <span className="hv-micro">{t('users.self_hint')}</span>
            ) : (
              <button
                className={
                  user.is_active ? 'hv-btn hv-btn--sm hv-btn--danger' : 'hv-btn hv-btn--sm'
                }
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    api.put(`/users/${user.id}/active?is_active=${!user.is_active}`, undefined),
                  )
                }
              >
                {t(user.is_active ? 'users.deactivate' : 'users.activate')}
              </button>
            )}
          </span>
          <span className="hv-row">
            <button className="hv-btn" type="button" onClick={onClose}>
              {t('common.close')}
            </button>
            <button
              className="hv-btn hv-btn--primary"
              type="button"
              disabled={busy || isSelf || role === user.role}
              onClick={() =>
                void run(() => api.put(`/users/${user.id}/role?role=${role}`, undefined))
              }
            >
              {t('common.save')}
            </button>
          </span>
        </>
      }
    >
      <dl className="admin-detail__facts">
        <dt>{t('users.name')}</dt>
        <dd>{user.display_name || '—'}</dd>
        <dt>{t('users.email')}</dt>
        <dd>{user.email}</dd>
        <dt>{t('users.created')}</dt>
        <dd>{new Date(user.created_at).toLocaleDateString(locale)}</dd>
      </dl>

      <label className="hv-field">
        <span className="hv-label">{t('users.role')}</span>
        <select
          className="hv-select"
          value={role}
          disabled={isSelf || busy}
          onChange={(event) => setRole(event.target.value as RoleName)}
        >
          {ROLES.map((one) => (
            <option key={one} value={one}>
              {translate(locale, `role.${one}` as MessageKey)}
            </option>
          ))}
        </select>
      </label>
    </Modal>
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
    /*
    A popup, like every other create in the console.

    This one has a second reason to be one: it is the only screen that takes a
    password, and a password field sitting inline in a list is a password field
    somebody fills in with the shop floor reading over their shoulder.
  */
    <Modal
      title={`${t('users.add')} :: ${t('users.title')}`}
      path="/IDENTITY/USERS/NEW"
      pathStatus="РОЛЬ — НАБОР ПРАВ, А НЕ ДОЛЖНОСТЬ"
      onClose={onCancel}
      footer={
        <>
          <span>ПАРОЛЬ ХРАНИТСЯ ХЭШЕМ ARGON2ID И НИКОГДА НЕ ПОКАЗЫВАЕТСЯ</span>
          <span className="hv-row">
            <button className="hv-btn" type="button" onClick={onCancel} disabled={busy}>
              {t('common.cancel')}
            </button>
            <button
              className="hv-btn hv-btn--primary"
              type="submit"
              form="staff-form"
              disabled={busy}
            >
              {t('common.save')}
            </button>
          </span>
        </>
      }
    >
      <form className="admin-form" id="staff-form" onSubmit={(event) => void submit(event)}>
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

        {error && (
          <p className="hv-hint hv-bad" role="alert">
            {error}
          </p>
        )}
      </form>
    </Modal>
  )
}
