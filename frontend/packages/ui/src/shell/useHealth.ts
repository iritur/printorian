import { useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'

import { api } from '../session/session'

/**
 * What the chrome's `STATUS ::` strip says.
 *
 * Four states, not two. `PROBING` is the interval before the first answer, and
 * it is distinct from `OFFLINE` for the same reason `SessionProvider.ready` is
 * distinct from "not signed in": a strip that reads OFFLINE for 200ms on every
 * load teaches people to ignore it, which is the one thing a status indicator
 * must not do.
 */
export type HealthStatus = 'PROBING' | 'ONLINE' | 'DEGRADED' | 'OFFLINE'

/** `/health/ready`, which names its own cause when a dependency is down. */
interface ReadyBody {
  status: 'ok' | 'degraded'
  checks: Record<string, 'ok' | 'failed'>
}

export interface Health {
  status: HealthStatus
  /** Per-dependency, for the settings screen's diagnostics section. */
  checks: Record<string, 'ok' | 'failed'>
  /** Round trip of the last probe, in ms. The kit's `.hv-health__ms`. */
  latencyMs: number | null
}

/** How often to re-probe. Slow on purpose: this is a heartbeat, not telemetry. */
const INTERVAL_MS = 30_000

/**
 * Probe `/health/ready` on an interval.
 *
 * Readiness rather than liveness: `/health` answers `ok` from a process whose
 * database has gone, which is precisely the outage an operator needs the strip
 * to show. The endpoint answers 503 when degraded, so `ApiError` is the normal
 * path for a real failure and not an exception to be swallowed silently.
 */
export function useHealth(): Health {
  const [health, setHealth] = useState<Health>({
    status: 'PROBING',
    checks: {},
    latencyMs: null,
  })

  useEffect(() => {
    let alive = true
    const controller = new AbortController()

    const probe = async () => {
      const started = performance.now()
      try {
        const body = await api.get<ReadyBody>('/health/ready', { signal: controller.signal })
        if (!alive) return
        setHealth({
          status: body.status === 'ok' ? 'ONLINE' : 'DEGRADED',
          checks: body.checks,
          latencyMs: Math.round(performance.now() - started),
        })
      } catch (error) {
        if (!alive || controller.signal.aborted) return
        // A 503 is the server answering — it is degraded, not unreachable, and
        // the distinction is what tells an operator whether to look at the farm
        // or at the network. Anything else means nothing answered at all.
        const degraded = error instanceof ApiError && error.status === 503
        setHealth({
          status: degraded ? 'DEGRADED' : 'OFFLINE',
          checks: {},
          latencyMs: Math.round(performance.now() - started),
        })
      }
    }

    void probe()
    const timer = setInterval(() => void probe(), INTERVAL_MS)

    return () => {
      alive = false
      controller.abort()
      clearInterval(timer)
    }
  }, [])

  return health
}
