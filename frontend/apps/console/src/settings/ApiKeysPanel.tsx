import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Locale, MessageKey } from '@printorian/ui'
import { api, translate, translateError, useSession } from '@printorian/ui'

/**
 * «Ключи API» (design/settings.html, «Доступ и безопасность»), over `/api-keys`.
 *
 * **The key is on screen once.** `POST /api-keys` is the only response that
 * carries it, the backend keeps only its hash, and this panel holds it in state
 * just long enough for a person to copy it — never in storage, never in the
 * list. The kit's foot says so, and so does the box it appears in.
 *
 * **What a key may hold comes from the backend** (`/api-keys/permissions`), so
 * the checkboxes cannot offer something the server would refuse. A key reads and
 * never writes; that rule is enforced server-side and only explained here.
 *
 * **«Использован» never shows a dash.** Every use is recorded, so a key with no
 * `last_used_at` has been measured as unused — «не использовался» is the
 * measurement, not a gap (ADR-0007 the other way round).
 *
 * Owner-only, like the routes: the panel draws nothing for anybody without
 * `manage_users`, rather than a table they would be refused.
 */

const MANAGE_USERS = 'manage_users'

/** `ApiKeyView`, mirrored by hand (frontend/CLAUDE.md). */
export interface ApiKey {
  id: string
  name: string
  prefix: string
  permissions: string[]
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

interface Issued {
  key: ApiKey
  secret: string
}

export function ApiKeysPanel({ locale }: { locale: Locale }) {
  const { actor } = useSession()
  const t = useCallback(
    (key: MessageKey, details?: Record<string, unknown>) => translate(locale, key, details),
    [locale],
  )
  const entitled = actor?.permissions.includes(MANAGE_USERS) ?? false

  const [keys, setKeys] = useState<ApiKey[]>([])
  const [grantable, setGrantable] = useState<string[]>([])
  const [name, setName] = useState('')
  const [chosen, setChosen] = useState<string[]>([])
  const [secret, setSecret] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  const fail = useCallback(
    (exc: unknown) =>
      setFailed(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : translate(locale, 'error.internal'),
      ),
    [locale],
  )

  const load = useCallback(async () => {
    try {
      const [rows, allowed] = await Promise.all([
        api.get<ApiKey[]>('/api-keys'),
        api.get<string[]>('/api-keys/permissions'),
      ])
      setKeys(rows)
      setGrantable(allowed)
      setFailed(null)
    } catch (exc: unknown) {
      fail(exc)
    }
  }, [fail])

  useEffect(() => {
    if (!entitled) return
    // From an async wrapper: `load` sets state, and a synchronous setState in an
    // effect body is the cascading render `react-hooks/set-state-in-effect` rejects.
    void (async () => {
      await load()
    })()
  }, [entitled, load])

  if (!entitled) return null

  const live = keys.filter((key) => key.revoked_at === null).length

  const issue = async () => {
    try {
      const issued = await api.post<Issued>('/api-keys', { name, permissions: chosen })
      setSecret(issued.secret)
      setName('')
      setChosen([])
      await load()
    } catch (exc: unknown) {
      fail(exc)
    }
  }

  const revoke = async (id: string) => {
    try {
      await api.post(`/api-keys/${id}/revoke`, {})
      await load()
    } catch (exc: unknown) {
      fail(exc)
    }
  }

  const when = (iso: string) => new Date(iso).toLocaleString(locale)

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>{t('settings.keys')}</span>
        <span className="hv-panel__aside">{t('settings.keys.live', { count: live })}</span>
      </div>
      <div className="hv-panel__body--none">
        {failed && <p className="hv-hint hv-bad">{failed}</p>}
        {secret !== null && (
          // A stack, so the key sits on a line of its own: run into the hint, the
          // end of a 51-character secret is easy to copy short.
          <div className="hv-frame hv-stack" role="status">
            <span className="hv-label">{t('settings.keys.secret')}</span>
            <div>
              <code className="hv-table__id">{secret}</code>
            </div>
            <span className="hv-micro">{t('settings.keys.secret_once')}</span>
            <div>
              <button className="hv-btn hv-btn--sm" type="button" onClick={() => setSecret(null)}>
                {t('settings.keys.done')}
              </button>
            </div>
          </div>
        )}
        {keys.length === 0 ? (
          <p className="hv-hint">{t('settings.keys.empty')}</p>
        ) : (
          <table className="hv-table">
            <thead>
              <tr>
                <th>{t('settings.keys.name')}</th>
                <th>{t('settings.keys.prefix')}</th>
                <th>{t('settings.keys.permissions')}</th>
                <th>{t('settings.keys.created')}</th>
                <th>{t('settings.keys.used')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => (
                <tr key={key.id}>
                  <td>{key.name}</td>
                  <td className="hv-table__id">{key.prefix}…</td>
                  <td>{key.permissions.join(', ')}</td>
                  <td>{new Date(key.created_at).toLocaleDateString(locale)}</td>
                  <td>{key.last_used_at ? when(key.last_used_at) : t('settings.keys.never')}</td>
                  <td>
                    {key.revoked_at === null ? (
                      <button
                        className="hv-btn hv-btn--sm hv-btn--danger"
                        type="button"
                        onClick={() => void revoke(key.id)}
                      >
                        {t('settings.keys.revoke')}
                      </button>
                    ) : (
                      <span className="hv-micro">
                        {t('settings.keys.revoked', { when: when(key.revoked_at) })}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="hv-panel__foot">
        <span>{t('settings.keys.foot')}</span>
        <form
          className="hv-row"
          onSubmit={(event) => {
            event.preventDefault()
            void issue()
          }}
        >
          <input
            aria-label={t('settings.keys.name')}
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
          {grantable.map((permission) => (
            <label key={permission} className="hv-micro">
              <input
                type="checkbox"
                checked={chosen.includes(permission)}
                onChange={(event) =>
                  setChosen((current) =>
                    event.target.checked
                      ? [...current, permission]
                      : current.filter((p) => p !== permission),
                  )
                }
              />{' '}
              {permission}
            </label>
          ))}
          <button className="hv-btn hv-btn--sm" type="submit" disabled={chosen.length === 0}>
            {t('settings.keys.create')}
          </button>
        </form>
      </div>
    </section>
  )
}
