import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { ApiClient, ApiError } from '@printorian/api-client'

/**
 * Who is signed in, and how to change that.
 *
 * The session lives in an httpOnly cookie set by the API, so this holds no token
 * and cannot leak one to a script. "Am I signed in?" is answered by asking
 * `/auth/me`, never by reading something the client could have forged.
 */

export interface Actor {
  user_id: string
  email: string
  display_name: string
  role: string
  locale: string
  permissions: string[]
}

interface Session {
  actor: Actor | null
  /** Distinguishes "not signed in" from "we have not asked yet". */
  ready: boolean
  signIn: (email: string, password: string) => Promise<void>
  register: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
}

const api = new ApiClient({ baseUrl: '/api' })
const SessionContext = createContext<Session | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [actor, setActor] = useState<Actor | null>(null)
  const [ready, setReady] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setActor(await api.get<Actor>('/auth/me'))
    } catch (exc: unknown) {
      // A 401 here is the normal "nobody is signed in" answer, not a failure.
      if (!(exc instanceof ApiError) || exc.status !== 401) {
        // Anything else is worth surfacing rather than silently treating as
        // signed-out, which would hide an outage behind a login screen.
        console.warn('session check failed', exc)
      }
      setActor(null)
    } finally {
      setReady(true)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const signIn = useCallback(
    async (email: string, password: string) => {
      await api.post('/auth/sign-in', { email, password })
      await refresh()
    },
    [refresh],
  )

  const register = useCallback(
    async (email: string, password: string) => {
      await api.post('/auth/register', { email, display_name: email, password })
      await api.post('/auth/sign-in', { email, password })
      await refresh()
    },
    [refresh],
  )

  const signOut = useCallback(async () => {
    await api.post('/auth/sign-out')
    setActor(null)
  }, [])

  const value = useMemo<Session>(
    () => ({ actor, ready, signIn, register, signOut }),
    [actor, ready, signIn, register, signOut],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): Session {
  const session = useContext(SessionContext)
  if (!session) throw new Error('useSession must be used inside <SessionProvider>')
  return session
}

export { api }
