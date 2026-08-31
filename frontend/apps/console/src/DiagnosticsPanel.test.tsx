/**
 * The diagnostics panel, on the claims that are the reason it exists.
 *
 * Every test here is about a way the panel could have reported something the
 * farm never said: a verdict it does not recognise drawn green, `degraded`
 * rendered as `failed`, an empty driver roster read as a fleet size, or a probe
 * that never answered leaving a list that looks clean because it is blank.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    handler: (() => Promise.reject(new Error('no handler'))) as (
      url: string,
      init?: RequestInit,
    ) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    state.handler(String(input), init)) as unknown as typeof fetch
  return state
})

import { DiagnosticsPanel } from './DiagnosticsPanel'

/** A body the panel can parse, whatever status code carried it. */
function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

interface Bodies {
  ready?: unknown
  workers?: unknown
}

function serve({ ready, workers }: Bodies) {
  net.handler = (url: string) => {
    if (url.endsWith('/health/ready')) {
      return ready === undefined
        ? Promise.reject(new TypeError('failed to fetch'))
        : Promise.resolve(jsonOk(ready))
    }
    if (url.endsWith('/health/workers')) {
      return workers === undefined
        ? Promise.reject(new TypeError('failed to fetch'))
        : Promise.resolve(jsonOk(workers))
    }
    return Promise.reject(new Error('unexpected request: ' + url))
  }
}

/** The `.hv-state` pill on the health row carrying `label`. */
function pill(label: string): HTMLElement {
  const row = screen.getByText(label).closest('.hv-health')
  if (!row) throw new Error(`no health row for «${label}»`)
  const state = row.querySelector('.hv-state')
  if (!state) throw new Error(`no state pill for «${label}»`)
  return state as HTMLElement
}

/** One of the three stat tiles: the figure it prints and the caption under it. */
function tile(label: string): { value: string; note: string; tone: string | null } {
  const card = screen.getByText(label).closest('.hv-stat')
  if (!card) throw new Error(`no stat card for «${label}»`)
  return {
    value: card.querySelector('.hv-stat__v')?.textContent ?? '',
    note: card.querySelector('.hv-micro')?.textContent ?? '',
    tone: card.getAttribute('data-tone'),
  }
}

const NO_LOOPS = { status: 'ok', loops: {}, drivers: {} }

/**
 * What `/health/workers` answers when the heartbeat store cannot be read.
 *
 * Not invented for the test: `Heartbeat.report()` iterates the compile-time
 * constant `LOOPS` and returns every one of them with `state="unknown"` when
 * there is no Redis client or the `mget` raised. So the roster is full and not
 * one reading in it was taken.
 */
const UNREADABLE_LOOPS = {
  status: 'degraded',
  loops: Object.fromEntries(
    ['intake', 'scheduler', 'telemetry', 'sla', 'postproduction', 'packaging', 'maintenance'].map(
      (loop) => [loop, { state: 'unknown', last_beat: null }],
    ),
  ),
  drivers: {},
}

beforeEach(() => {
  serve({
    ready: { status: 'ok', checks: { database: 'ok' } },
    workers: NO_LOOPS,
  })
})

describe('the readiness checks', () => {
  it('draws each verdict as itself, with degraded distinct from failed', async () => {
    serve({
      ready: {
        status: 'degraded',
        checks: { database: 'ok', wal_archiving: 'degraded', event_relay: 'failed' },
      },
      workers: NO_LOOPS,
    })

    render(<DiagnosticsPanel locale="ru" />)

    await screen.findByText('База данных')

    // Three verdicts, three words and three tones. The word is the load-bearing
    // half: «serving fine, guarantee not holding» and «this dependency is gone»
    // are different instructions to whoever is reading, and a reader who cannot
    // separate amber from red gets nothing from the colour alone.
    expect(pill('База данных')).toHaveAttribute('data-state', 'idle')
    expect(pill('База данных')).toHaveTextContent('В норме')

    expect(pill('Архивация WAL')).toHaveAttribute('data-state', 'paused')
    expect(pill('Архивация WAL')).toHaveTextContent('Деградация')

    expect(pill('Ретранслятор событий')).toHaveAttribute('data-state', 'error')
    expect(pill('Ретранслятор событий')).toHaveTextContent('Сбой')

    // And the three tones really are three, not two spellings of one.
    const tones = new Set(
      ['База данных', 'Архивация WAL', 'Ретранслятор событий'].map((label) =>
        pill(label).getAttribute('data-state'),
      ),
    )
    expect(tones.size).toBe(3)
  })

  it('reads a verdict it does not recognise as unknown rather than as healthy', async () => {
    // The mapping is a whitelist. A `!== 'failed'` would have drawn this green,
    // which is the flattering direction root CLAUDE.md §1 is written about.
    serve({
      ready: { status: 'ok', checks: { database: 'quiescent' } },
      workers: NO_LOOPS,
    })

    render(<DiagnosticsPanel locale="ru" />)

    await screen.findByText('База данных')
    expect(pill('База данных')).toHaveAttribute('data-state', 'offline')
    expect(pill('База данных')).toHaveTextContent('Не измерено')
    // And it is not counted into the tile either way: «0 ИЗ 1 ПРОВЕРОК» reads as
    // a failed check and «1 ИЗ 1» as a passing one, and the check did neither.
    expect(tile('Готовность').note).toBe('НЕ ИЗМЕРЕНО')
  })

  it('counts the denominator from the checks that answered, not from a roster', async () => {
    // `event_relay` is reported only where a relay is configured, so a fixed
    // total would leave a deployment without one permanently one check short.
    serve({
      ready: { status: 'degraded', checks: { database: 'ok', wal_archiving: 'degraded' } },
      workers: NO_LOOPS,
    })

    render(<DiagnosticsPanel locale="ru" />)

    expect(await screen.findByText('1 ИЗ 2 ПРОВЕРОК')).toBeInTheDocument()
  })

  it('says nothing was measured when the probe does not answer', async () => {
    serve({ ready: undefined, workers: undefined })

    render(<DiagnosticsPanel locale="ru" />)

    // All three panels say it — readiness, the loops and the drivers each lost
    // their own probe, and each says so in its own body.
    expect(await screen.findAllByText(/Состояние подсистем не измерено/)).toHaveLength(3)
    // Not one green pill on the screen, and not one silent empty list either:
    // an unreachable probe is the case where a blank panel reads as «all clear».
    expect(document.querySelectorAll('[data-state="idle"]')).toHaveLength(0)
    expect(screen.getAllByText('НЕ ИЗМЕРЕНО').length).toBeGreaterThan(0)
  })

  it('does not report a round trip for a probe that never answered', async () => {
    // `probe()` times its catch block as well as its success path, so an
    // unreachable readiness check still carries a `latencyMs`. It is a real
    // number and it measures the wrong thing: how long the failure took, under
    // a label that reads «ОТВЕТ … МС». The foot must stay silent rather than
    // report a round trip nobody completed.
    serve({ ready: undefined, workers: undefined })

    render(<DiagnosticsPanel locale="ru" />)

    await screen.findAllByText(/Состояние подсистем не измерено/)
    expect(screen.queryByText(/ОТВЕТ\s*\d+\s*МС/)).not.toBeInTheDocument()
  })

  it('keeps the body of a 503, because that is the answer this panel is for', async () => {
    // `ApiClient` throws on a non-2xx and `readErrorBody` keeps only
    // `{code}`-shaped payloads, so going through it would have blanked the panel
    // exactly when a check had failed. The probe reads the body regardless.
    net.handler = (url: string) => {
      const body = url.endsWith('/health/ready')
        ? { status: 'degraded', checks: { database: 'failed' } }
        : NO_LOOPS
      return Promise.resolve({
        ok: false,
        status: 503,
        json: () => Promise.resolve(body),
      } as unknown as Response)
    }

    render(<DiagnosticsPanel locale="ru" />)

    await screen.findByText('База данных')
    expect(pill('База данных')).toHaveAttribute('data-state', 'error')
  })

  it('re-probes on «Прогнать заново»', async () => {
    let asked = 0
    net.handler = (url: string) => {
      if (url.endsWith('/health/ready')) {
        asked += 1
        return Promise.resolve(jsonOk({ status: 'ok', checks: { database: 'ok' } }))
      }
      return Promise.resolve(jsonOk(NO_LOOPS))
    }

    render(<DiagnosticsPanel locale="ru" />)

    await screen.findByText('База данных')
    await waitFor(() => expect(asked).toBe(1))

    await userEvent.click(screen.getByRole('button', { name: 'Прогнать заново' }))
    await waitFor(() => expect(asked).toBe(2))
  })
})

describe('the worker loops', () => {
  it('separates a loop that is sweeping from one that has stopped', async () => {
    serve({
      ready: { status: 'ok', checks: { database: 'ok' } },
      workers: {
        status: 'degraded',
        loops: {
          scheduler: { state: 'beating', last_beat: '2026-08-31T09:15:00Z' },
          sla: { state: 'stale', last_beat: null },
          telemetry: { state: 'unknown', last_beat: null },
        },
        drivers: {},
      },
    })

    render(<DiagnosticsPanel locale="ru" />)

    await screen.findByText('Планировщик')
    expect(pill('Планировщик')).toHaveAttribute('data-state', 'idle')
    expect(pill('Обход SLA')).toHaveAttribute('data-state', 'error')
    // `unknown` is the heartbeat store being unreadable. Not healthy, and not a
    // wedged loop either — claiming either would be a reading nobody took.
    expect(pill('Опрос телеметрии')).toHaveAttribute('data-state', 'offline')

    // Three loops arrived and two of them were readings. One of those two is
    // beating, so the tile is «1 из 2» and says separately that a third loop was
    // not measured — the denominator is what answered, and the row that did not
    // answer is in neither half of the fraction.
    expect(tile('Циклы в работе').value).toBe('1')
    expect(tile('Циклы в работе').note).toBe('ИЗ 2 НАБЛЮДАЕМЫХ · 1 НЕ ИЗМЕРЕНО')
  })

  it('withholds the figure entirely when no loop reading was taken', async () => {
    // The case this tile is most likely to be read in, and the one it used to
    // lie in: the heartbeat store is unreadable, so the roster arrives complete
    // and empty of readings. `loops.length === 0 ? '—' : String(beating)` drew
    // «0» over «ИЗ 7 НАБЛЮДАЕМЫХ» here — seven loops reported stopped, on the
    // evidence that nobody looked at any of them (root CLAUDE.md §1).
    serve({ ready: { status: 'ok', checks: { database: 'ok' } }, workers: UNREADABLE_LOOPS })

    render(<DiagnosticsPanel locale="ru" />)

    await screen.findByText('Планировщик')
    expect(tile('Циклы в работе').value).toBe('—')
    expect(tile('Циклы в работе').note).toBe('НЕ ИЗМЕРЕНО')
    // Neither a zero anywhere on the tile, nor a denominator taken from the
    // seven rows that measured nothing.
    expect(tile('Циклы в работе').value).not.toContain('0')
    expect(tile('Циклы в работе').note).not.toContain('7')
    // And no tone: green, amber and red are all claims, and none was earned.
    expect(tile('Циклы в работе').tone).toBeNull()
  })

  it('keeps «some measured» apart from «all fine» on the tile itself', async () => {
    // Three states, three tiles, and the reader has to be able to tell them
    // apart from the figure alone — «2» with every loop measured means the farm
    // is sweeping, and «2» with five loops unread means almost nothing.
    serve({
      ready: { status: 'ok', checks: { database: 'ok' } },
      workers: {
        status: 'ok',
        loops: {
          scheduler: { state: 'beating', last_beat: '2026-08-31T09:15:00Z' },
          sla: { state: 'beating', last_beat: '2026-08-31T09:15:00Z' },
        },
        drivers: {},
      },
    })

    render(<DiagnosticsPanel locale="ru" />)

    await screen.findByText('Планировщик')
    expect(tile('Циклы в работе').value).toBe('2')
    expect(tile('Циклы в работе').note).toBe('ИЗ 2 НАБЛЮДАЕМЫХ')
    // Everything observed and everything healthy is the one case that earns the
    // green tone, which is what stops the two «2»s reading alike.
    expect(tile('Циклы в работе').tone).toBe('good')
  })

  it('shows an em dash for a loop that has never beaten', async () => {
    serve({
      ready: { status: 'ok', checks: { database: 'ok' } },
      workers: { status: 'degraded', loops: { sla: { state: 'stale', last_beat: null } }, drivers: {} },
    })

    render(<DiagnosticsPanel locale="ru" />)

    const row = (await screen.findByText('Обход SLA')).closest('.hv-health')
    expect(row?.querySelector('.hv-health__ms')?.textContent).toBe('—')
  })
})

describe('the drivers', () => {
  it('reads an empty roster as «nothing published», never as «no printers»', async () => {
    render(<DiagnosticsPanel locale="ru" />)

    expect(await screen.findByText(/Это не значит, что у фермы нет принтеров/)).toBeInTheDocument()
    // And the tile withholds a figure rather than reporting zero connected.
    expect(screen.getByText('Принтеры на связи').parentElement?.textContent).toContain('—')
  })

  it('withholds the figure for a roster whose every reading has lapsed', async () => {
    // The roster and the readings are two Redis windows with different lives, so
    // a worker that has gone leaves the printers named and every state
    // `unknown`. «0 из 3 подключено» would be a claim that three printers are
    // off the air; what the panel knows is that it heard about none of them.
    serve({
      ready: { status: 'ok', checks: { database: 'ok' } },
      workers: {
        status: 'ok',
        loops: {},
        drivers: {
          'p-1': { name: 'P-01', state: 'unknown', code: null, since: null },
          'p-2': { name: 'P-02', state: 'unknown', code: null, since: null },
          'p-3': { name: 'P-03', state: 'unknown', code: null, since: null },
        },
      },
    })

    render(<DiagnosticsPanel locale="ru" />)

    await screen.findByText('P-01')
    // The rows are drawn — the farm does have three printers and that is worth
    // saying — and the tile above them declines to summarise readings nobody
    // took, rather than summarising them as zero.
    expect(tile('Принтеры на связи').value).toBe('—')
    expect(tile('Принтеры на связи').note).toBe('НЕ ИЗМЕРЕНО')
    expect(tile('Принтеры на связи').tone).toBeNull()
  })

  it('counts the printers that reported, and says how many did not', async () => {
    serve({
      ready: { status: 'ok', checks: { database: 'ok' } },
      workers: {
        status: 'ok',
        loops: {},
        drivers: {
          'p-1': { name: 'P-01', state: 'connected', code: null, since: null },
          'p-8': { name: 'P-08', state: 'unavailable', code: null, since: null },
          'p-9': { name: 'P-09', state: 'unknown', code: null, since: null },
        },
      },
    })

    render(<DiagnosticsPanel locale="ru" />)

    await screen.findByText('P-01')
    expect(tile('Принтеры на связи').value).toBe('1')
    expect(tile('Принтеры на связи').note).toBe('ИЗ 2 НАБЛЮДАЕМЫХ · 1 НЕ ИЗМЕРЕНО')
  })

  it('names the printer, its state and the code behind an unavailable one', async () => {
    serve({
      ready: { status: 'ok', checks: { database: 'ok' } },
      workers: {
        status: 'ok',
        loops: {},
        drivers: {
          'p-1': { name: 'P-01', state: 'connected', code: null, since: null },
          'p-8': {
            name: 'P-08',
            state: 'unavailable',
            code: 'error.driver.unavailable',
            since: '2026-08-31T05:02:00Z',
          },
          'p-9': { name: 'P-09', state: 'unknown', code: null, since: null },
        },
      },
    })

    render(<DiagnosticsPanel locale="ru" />)

    await screen.findByText('P-01')
    expect(pill('P-01')).toHaveAttribute('data-state', 'idle')
    expect(pill('P-08')).toHaveAttribute('data-state', 'error')
    // The roster still names it and its reading has lapsed — the case the two
    // Redis windows exist to produce, and it must not read as connected.
    expect(pill('P-09')).toHaveAttribute('data-state', 'offline')
    expect(screen.getByText('Принтер недоступен')).toBeInTheDocument()
  })
})
