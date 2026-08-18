import { describe, expect, it, vi } from 'vitest'

import { CLOSE_UNAUTHORIZED, EventStream, backoffDelay, streamUrl } from './stream'
import type { StreamStatus } from './stream'
import type { LiveEvent } from './types'

/** A WebSocket stand-in whose lifecycle the test drives by hand. */
class FakeSocket {
  static instances: FakeSocket[] = []

  onopen: (() => void) | null = null
  onmessage: ((message: MessageEvent) => void) | null = null
  onclose: ((closed: CloseEvent) => void) | null = null
  onerror: (() => void) | null = null
  closed = false

  constructor(readonly url: string) {
    FakeSocket.instances.push(this)
  }

  close(): void {
    this.closed = true
  }

  // -- test drivers
  open(): void {
    this.onopen?.()
  }

  deliver(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent)
  }

  deliverRaw(data: string): void {
    this.onmessage?.({ data } as MessageEvent)
  }

  drop(code = 1006): void {
    this.onclose?.({ code } as CloseEvent)
  }
}

interface Harness {
  stream: EventStream
  events: LiveEvent[]
  statuses: StreamStatus[]
  resyncs: number
  runPending: () => void
  sockets: FakeSocket[]
}

function harness(): Harness {
  FakeSocket.instances = []
  const events: LiveEvent[] = []
  const statuses: StreamStatus[] = []
  const state = { resyncs: 0 }
  let pending: (() => void) | null = null

  const stream = new EventStream({
    url: 'ws://test/api/ws/events',
    onEvent: (event) => events.push(event),
    onStatus: (status) => statuses.push(status),
    onResync: () => {
      state.resyncs += 1
    },
    socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
    schedule: (run) => {
      pending = run
      return 1
    },
    cancel: () => {
      pending = null
    },
    random: () => 0.5,
  })

  return {
    stream,
    events,
    statuses,
    get resyncs() {
      return state.resyncs
    },
    runPending: () => {
      const run = pending
      pending = null
      run?.()
    },
    sockets: FakeSocket.instances,
  }
}

const stateChanged = {
  name: 'fleet.printer_state_changed',
  event_id: 'e1',
  occurred_at: '2026-03-02T09:00:00+00:00',
  printer_id: 'p1',
  printer_name: 'p1s-01',
  from_state: 'idle',
  to_state: 'printing',
}

describe('EventStream', () => {
  it('delivers events once connected', () => {
    const h = harness()
    h.stream.start()
    h.sockets[0]!.open()
    h.sockets[0]!.deliver(stateChanged)

    expect(h.events).toHaveLength(1)
    expect(h.events[0]!.name).toBe('fleet.printer_state_changed')
    expect(h.stream.currentStatus).toBe('live')
  })

  it('resyncs on the first connection, not only on reconnects', () => {
    // The initial load has the same gap problem as a reconnect: the client knows
    // nothing until it fetches.
    const h = harness()
    h.stream.start()
    h.sockets[0]!.open()

    expect(h.resyncs).toBe(1)
  })

  it('resyncs again after a reconnect, because the gap lost events', () => {
    const h = harness()
    h.stream.start()
    h.sockets[0]!.open()

    h.sockets[0]!.drop()
    expect(h.stream.currentStatus).toBe('reconnecting')

    h.runPending()
    h.sockets[1]!.open()

    expect(h.resyncs).toBe(2)
    expect(h.stream.currentStatus).toBe('live')
  })

  it('reports that it is no longer live the moment the socket drops', () => {
    // The screen must be able to say "stale" instead of showing the last known
    // state as though it were current.
    const h = harness()
    expect(h.stream.currentStatus).toBe('connecting')

    h.stream.start()
    h.sockets[0]!.open()
    h.sockets[0]!.drop()

    // 'connecting' is the initial value, so it is not re-emitted as a change.
    expect(h.statuses).toEqual(['live', 'reconnecting'])
    expect(h.stream.currentStatus).toBe('reconnecting')
  })

  it('does not emit a status that has not changed', () => {
    // Every status hop re-renders a subscribed screen; a repeated 'reconnecting'
    // on each failed attempt would be churn that tells the user nothing new.
    const h = harness()
    h.stream.start()
    h.sockets[0]!.drop()
    h.runPending()
    h.sockets[1]!.drop()

    expect(h.statuses).toEqual(['reconnecting'])
  })

  it('gives up on a refused handshake instead of hammering the API', () => {
    const h = harness()
    h.stream.start()
    h.sockets[0]!.drop(CLOSE_UNAUTHORIZED)

    expect(h.stream.currentStatus).toBe('denied')
    h.runPending()
    expect(h.sockets).toHaveLength(1)
  })

  it('survives a malformed frame', () => {
    const h = harness()
    h.stream.start()
    h.sockets[0]!.open()

    h.sockets[0]!.deliverRaw('{not json')
    h.sockets[0]!.deliver({ missing: 'a name' })
    h.sockets[0]!.deliver(stateChanged)

    expect(h.events).toHaveLength(1)
    expect(h.stream.currentStatus).toBe('live')
  })

  it('passes through an event name it has never heard of', () => {
    // A backend deployed ahead of this build must not break the stream.
    const h = harness()
    h.stream.start()
    h.sockets[0]!.open()
    h.sockets[0]!.deliver({ name: 'attention.something_new', event_id: 'e', occurred_at: 'x' })

    expect(h.events[0]!.name).toBe('attention.something_new')
  })

  it('does not reconnect after stop', () => {
    const h = harness()
    h.stream.start()
    h.sockets[0]!.open()
    h.stream.stop()

    // A close event racing the unmount must not resurrect the stream.
    h.sockets[0]!.onclose?.({ code: 1006 } as CloseEvent)
    h.runPending()

    expect(h.sockets).toHaveLength(1)
    expect(h.sockets[0]!.closed).toBe(true)
    expect(h.stream.currentStatus).toBe('closed')
  })

  it('backs off further on each successive failure', () => {
    const h = harness()
    h.stream.start()

    h.sockets[0]!.drop()
    h.runPending()
    h.sockets[1]!.drop()
    h.runPending()

    expect(h.sockets).toHaveLength(3)
  })

  it('resets the backoff once a connection succeeds', () => {
    // Otherwise a long-lived client that dropped once at 3am waits the maximum
    // delay for every future blip.
    const h = harness()
    h.stream.start()
    h.sockets[0]!.drop()
    h.runPending()
    h.sockets[1]!.open()
    h.sockets[1]!.drop()

    expect(backoffDelay(0, () => 0.5)).toBe(375)
  })
})

describe('backoffDelay', () => {
  it('grows exponentially and then stops at the ceiling', () => {
    const full = (attempt: number) => backoffDelay(attempt, () => 1)
    expect(full(0)).toBe(500)
    expect(full(1)).toBe(1000)
    expect(full(2)).toBe(2000)
    expect(full(20)).toBe(15_000)
  })

  it('jitters so a farm of displays does not retry in lockstep', () => {
    expect(backoffDelay(3, () => 0)).toBeLessThan(backoffDelay(3, () => 1))
    expect(backoffDelay(3, () => 0)).toBeGreaterThan(0)
  })
})

describe('streamUrl', () => {
  it('follows the page protocol so a TLS deployment is not mixed content', () => {
    expect(streamUrl('/api/ws/events', { protocol: 'https:', host: 'farm.local' } as Location)).toBe(
      'wss://farm.local/api/ws/events',
    )
    expect(
      streamUrl('/api/ws/events', { protocol: 'http:', host: '127.0.0.1:5173' } as Location),
    ).toBe('ws://127.0.0.1:5173/api/ws/events')
  })
})

describe('unmount safety', () => {
  it('cancels a pending retry', () => {
    const cancel = vi.fn()
    const stream = new EventStream({
      url: 'ws://test',
      onEvent: () => {},
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
      schedule: () => 7,
      cancel,
    })
    FakeSocket.instances = []
    stream.start()
    FakeSocket.instances[0]!.drop()
    stream.stop()

    expect(cancel).toHaveBeenCalledWith(7)
  })
})

describe('credential subprotocols', () => {
  it('offers them on the handshake', () => {
    // How a client with no session cookie authenticates: no WebSocket client can
    // set an Authorization header, and a token in the query string is logged.
    let seen: string[] | undefined
    const stream = new EventStream({
      url: 'ws://test/ws/events',
      protocols: ['printorian.v1', 'bearer.abc123'],
      onEvent: () => {},
      socketFactory: (url, protocols) => {
        seen = protocols
        return new FakeSocket(url) as unknown as WebSocket
      },
      schedule: () => 1,
      cancel: () => {},
    })
    stream.start()

    expect(seen).toEqual(['printorian.v1', 'bearer.abc123'])
    stream.stop()
  })

  it('offers none when there are none, so the cookie path is unchanged', () => {
    let seen: string[] | undefined = ['unset']
    const stream = new EventStream({
      url: 'ws://test/ws/events',
      onEvent: () => {},
      socketFactory: (url, protocols) => {
        seen = protocols
        return new FakeSocket(url) as unknown as WebSocket
      },
      schedule: () => 1,
      cancel: () => {},
    })
    stream.start()

    expect(seen).toBeUndefined()
    stream.stop()
  })

  it('re-offers them after a reconnect', () => {
    // A reconnect that dropped the credential would silently downgrade to an
    // anonymous handshake and be refused for the rest of the session.
    const offered: (string[] | undefined)[] = []
    // A holder object, because TypeScript cannot see the assignment that happens
    // inside `schedule` and would otherwise narrow the variable to `null`.
    const timer: { run: (() => void) | null } = { run: null }
    const sockets: FakeSocket[] = []
    const stream = new EventStream({
      url: 'ws://test/ws/events',
      protocols: ['printorian.v1', 'bearer.abc123'],
      onEvent: () => {},
      socketFactory: (url, protocols) => {
        offered.push(protocols)
        const socket = new FakeSocket(url)
        sockets.push(socket)
        return socket as unknown as WebSocket
      },
      schedule: (run) => {
        timer.run = run
        return 1
      },
      cancel: () => {},
      random: () => 0.5,
    })
    stream.start()
    sockets[0]!.drop()
    timer.run?.()

    expect(offered).toHaveLength(2)
    expect(offered[1]).toEqual(['printorian.v1', 'bearer.abc123'])
  })
})
