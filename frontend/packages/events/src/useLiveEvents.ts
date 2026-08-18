import { useEffect, useRef, useState } from 'react'

import { EventStream, streamUrl } from './stream'
import type { StreamStatus } from './stream'
import type { LiveEvent } from './types'

export interface UseLiveEventsOptions {
  onEvent: (event: LiveEvent) => void
  /**
   * Refetch full state here. Fires on first connect and on every reconnect,
   * because events published while the socket was down are simply gone.
   */
  onResync?: () => void
  /** Defaults to the same-origin `/api/ws/events`. */
  url?: string
  /** Credential-carrying subprotocols for a client with no session cookie. */
  protocols?: string[]
  /**
   * Gate on "is anyone entitled to watch yet". Mounting the stream before the
   * session resolves produces a guaranteed 4401 and a `denied` status that never
   * recovers, so the fleet screen passes `ready && canViewProduction` here.
   */
  enabled?: boolean
}

/**
 * Subscribe to the live event stream for the lifetime of a component.
 *
 * Handler identity is deliberately *not* a dependency of the connection. Callers
 * write `onEvent={(e) => …}` inline, which is a new function every render; if
 * that drove the effect, the socket would tear down and reopen on every render
 * and the stream would never stay up long enough to deliver anything. The
 * handlers live in a ref that each render refreshes, so the newest closure is
 * always called while the connection itself only depends on where it points.
 */
export function useLiveEvents(options: UseLiveEventsOptions): StreamStatus {
  const { url, enabled = true, protocols } = options
  const [status, setStatus] = useState<StreamStatus>(enabled ? 'connecting' : 'closed')

  const handlers = useRef(options)
  handlers.current = options

  // `protocols` is an array, so it has a new identity on every render. Keying
  // the effect on its contents means a reconnect happens when the *token*
  // changes and not merely because the component re-rendered.
  const protocolKey = (protocols ?? []).join(',')

  useEffect(() => {
    if (!enabled) {
      setStatus('closed')
      return
    }

    const offered = protocolKey ? protocolKey.split(',') : []
    const stream = new EventStream({
      url: url ?? streamUrl(),
      ...(offered.length ? { protocols: offered } : {}),
      onEvent: (event) => handlers.current.onEvent(event),
      onResync: () => handlers.current.onResync?.(),
      onStatus: setStatus,
    })
    stream.start()
    return () => stream.stop()
  }, [url, enabled, protocolKey])

  return status
}
