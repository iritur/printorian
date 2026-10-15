/**
 * The promo page's upload promise, held to the limit that actually applies.
 *
 * «РАСЧЁТ БЕЗ РЕГИСТРАЦИИ · STL · ДО N МБ» promises a visitor who has not signed
 * in a quote for a file up to N MB. Since security review §5(c) a visitor has
 * their own, lower ceiling (`anonymous_upload_bytes`), and a page still quoting
 * the signed-in limit would invite a 200 MB upload the server then refuses at 50.
 * The two figures are served apart here so the page cannot pass by reading the
 * wrong one.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const net = vi.hoisted(() => {
  const state = {
    handler: (() => Promise.reject(new Error('no handler'))) as (url: string) => Promise<unknown>,
  }
  globalThis.fetch = ((input: RequestInfo | URL) =>
    state.handler(String(input))) as unknown as typeof fetch
  return state
})

import { PromoPage } from './PromoPage'

function jsonOk(body: unknown): Response {
  return { ok: true, status: 200, json: () => Promise.resolve(body) } as unknown as Response
}

const MB = 1024 * 1024

describe('the promo page', () => {
  it('promises a visitor the anonymous upload limit, not the signed-in one', async () => {
    net.handler = (url: string) => {
      if (url.includes('/public/stats'))
        return Promise.resolve(
          jsonOk({
            window_days: 30,
            orders_delivered: 0,
            on_time_percent: null,
            print_hours: null,
            failure_percent: null,
            has_history: false,
            printers_total: 0,
            printers_printing: 0,
            next_free_minutes: null,
            catalog_models: 0,
            max_upload_bytes: 200 * MB,
            anonymous_upload_bytes: 50 * MB,
          }),
        )
      if (url.includes('/catalog')) return Promise.resolve(jsonOk({ rows: [] }))
      return Promise.reject(new Error('unexpected request: ' + url))
    }

    render(<PromoPage locale="ru" onStart={() => {}} onCabinet={() => {}} onCatalog={() => {}} />)

    expect(await screen.findByText(/ДО 50 МБ/)).toBeInTheDocument()
    expect(screen.queryByText(/ДО 200 МБ/)).toBeNull()
  })
})
