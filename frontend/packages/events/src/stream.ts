import type { LiveEvent } from './types'

/**
 * A reconnecting subscription to the server's live event stream.
 *
 * ## The gap problem
 *
 * A WebSocket that drops loses every event published while it was down. If the
 * client simply resumed applying deltas on reconnect, the screen would keep
 * looking authoritative while quietly diverging from the farm — a printer that
 * finished during the gap would show as still printing, indefinitely. That is
 * the same class of failure as V1 inventing data for an unreachable machine:
 * the display is confident and wrong.
 *
 * So this class does not treat the socket as the source of truth. It calls
 * `onResync` on *every* successful connection, including the first, and the
 * subscriber answers it by refetching the full state over HTTP. The socket
 * carries deltas between resyncs; the resync makes the gap harmless.
 *
 * ## Status is part of the contract
 *
 * `onStatus` exists so the UI can say "not live" rather than silently showing
 * the last thing it heard. A stale table that admits it is stale is honest; one
 * that does not is a bug report waiting to happen.
 */

export type StreamStatus =
  | 'connecting'
  /** Connected; events are flowing and the view is current. */
  | 'live'
  /** Dropped, retrying with backoff. What is on screen may already be stale. */
  | 'reconnecting'
  /** The server refused the handshake. Not retried — see `CLOSE_UNAUTHORIZED`. */
  | 'denied'
  /** Stopped by the caller. */
  | 'closed'

/** Sent by the API when the caller may not watch. Matches `ws.py`. */
export const CLOSE_UNAUTHORIZED = 4401

export interface StreamOptions {
  /** Same-origin path. The session cookie authenticates the handshake. */
  url: string
  /**
   * Subprotocols offered on the handshake.
   *
   * How a client that has no session cookie presents its credential: no
   * WebSocket client can set an `Authorization` header, and a token in the query
   * string would be recorded by every proxy in between. The desktop console
   * passes `['printorian.v1', 'bearer.<token>']`; the storefront passes nothing
   * and is authenticated by its cookie.
   */
  protocols?: string[]
  onEvent: (event: LiveEvent) => void
  /**
   * Fired on every (re)connection, including the first. Refetch full state here.
   * Without this the view drifts across a reconnect — see the class docstring.
   */
  onResync?: () => void
  onStatus?: (status: StreamStatus) => void
  /** Injected in tests. Defaults to the global. */
  socketFactory?: (url: string, protocols?: string[]) => WebSocket
  /** Injected in tests so backoff does not make the suite slow. */
  schedule?: (run: () => void, delayMs: number) => number
  cancel?: (handle: number) => void
  /** Injected in tests to make backoff deterministic. */
  random?: () => number
}

const BASE_DELAY_MS = 500
const MAX_DELAY_MS = 15_000

/**
 * Exponential backoff with jitter.
 *
 * The jitter matters more than it looks: a farm's worth of shop-floor displays
 * all reconnect when the API restarts, and without it they retry in lockstep and
 * hit the server as one synchronized wave every time.
 */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const ceiling = Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS)
  return Math.round(ceiling / 2 + random() * (ceiling / 2))
}

/** Build the stream URL for the current origin. */
export function streamUrl(path = '/api/ws/events', location = globalThis.location): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}${path}`
}

export class EventStream {
  private socket: WebSocket | null = null
  private timer: number | null = null
  private attempt = 0
  private stopped = false
  private status: StreamStatus = 'connecting'

  private readonly schedule: (run: () => void, delayMs: number) => number
  private readonly cancel: (handle: number) => void
  private readonly random: () => number
  private readonly newSocket: (url: string, protocols?: string[]) => WebSocket

  constructor(private readonly options: StreamOptions) {
    this.schedule = options.schedule ?? ((run, ms) => globalThis.setTimeout(run, ms) as never)
    this.cancel = options.cancel ?? ((handle) => globalThis.clearTimeout(handle))
    this.random = options.random ?? Math.random
    this.newSocket =
      options.socketFactory ??
      ((url, protocols) => (protocols?.length ? new WebSocket(url, protocols) : new WebSocket(url)))
  }

  start(): void {
    if (this.stopped) return
    this.open()
  }

  stop(): void {
    this.stopped = true
    if (this.timer !== null) {
      this.cancel(this.timer)
      this.timer = null
    }
    // Drop the handlers before closing: `onclose` fires asynchronously and would
    // otherwise schedule a reconnect for a stream the caller has already ended
    // — the classic unmount-then-resurrect leak.
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.onopen = null
      socket.onmessage = null
      socket.onclose = null
      socket.onerror = null
      socket.close()
    }
    this.setStatus('closed')
  }

  get currentStatus(): StreamStatus {
    return this.status
  }

  private open(): void {
    this.setStatus(this.attempt === 0 ? 'connecting' : 'reconnecting')

    let socket: WebSocket
    try {
      socket = this.newSocket(this.options.url, this.options.protocols)
    } catch {
      // Construction itself can throw (a bad URL, a blocked scheme). Treat it as
      // a failed attempt rather than letting it escape into a React effect.
      this.retry()
      return
    }
    this.socket = socket

    socket.onopen = () => {
      this.attempt = 0
      this.setStatus('live')
      // Full refetch, every time. See the class docstring.
      this.options.onResync?.()
    }

    socket.onmessage = (message: MessageEvent) => {
      const parsed = this.parse(message.data)
      if (parsed) this.options.onEvent(parsed)
    }

    socket.onclose = (closed: CloseEvent) => {
      if (this.stopped) return
      if (closed.code === CLOSE_UNAUTHORIZED) {
        // Retrying a refusal would hammer the API forever and never succeed.
        // The caller signs in again; that remounts the stream.
        this.socket = null
        this.setStatus('denied')
        return
      }
      this.retry()
    }

    // An error is always followed by a close, which is where reconnection is
    // handled. Swallowing it here keeps it off the console as an unhandled event.
    socket.onerror = () => {}
  }

  private parse(data: unknown): LiveEvent | null {
    if (typeof data !== 'string') return null
    try {
      const value: unknown = JSON.parse(data)
      if (typeof value !== 'object' || value === null) return null
      const candidate = value as Partial<LiveEvent>
      // A frame without a name cannot be routed. Dropping one bad frame is
      // correct; tearing down the stream over it is not.
      return typeof candidate.name === 'string' ? (value as LiveEvent) : null
    } catch {
      return null
    }
  }

  private retry(): void {
    this.socket = null
    this.setStatus('reconnecting')
    const delay = backoffDelay(this.attempt, this.random)
    this.attempt += 1
    this.timer = this.schedule(() => {
      this.timer = null
      if (!this.stopped) this.open()
    }, delay)
  }

  private setStatus(status: StreamStatus): void {
    if (this.status === status) return
    this.status = status
    this.options.onStatus?.(status)
  }
}
