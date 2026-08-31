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

const NO_LOOPS = { status: 'ok', loops: {}, drivers: {} }

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

    // One of three is beating, and the tile says so against what it observed.
    expect(screen.getByText('ИЗ 3 НАБЛЮДАЕМЫХ')).toBeInTheDocument()
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
