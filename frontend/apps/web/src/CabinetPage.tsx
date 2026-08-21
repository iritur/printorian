import { useEffect, useState } from 'react'

import type { Locale } from '@printorian/ui'
import { AuthPanel, api, translate, useChrome, useSession } from '@printorian/ui'

import { CabinetAside } from './CabinetAside'
import { CabinetHistory } from './CabinetHistory'
import { CabinetPipeline } from './CabinetPipeline'
import { CabinetSummary } from './CabinetSummary'
import { CLOSED, NONE, fullStamp, pipeline, stamp, stateOf } from './cabinet'
import type { Order, Progress } from './cabinet'

/**
 * «Мои заказы» — `design/cabinet.html`, the scenario's step 9.
 *
 * One order at a time, chosen from the rail on the left. That is the kit's
 * shape and it is the right one: tracking is a question about *one* order —
 * where is it, when will it arrive, what happens if it is late — and a table of
 * fourteen rows answers none of those without a second click anyway. The
 * account screen's «Заказы» section is the table, for when the question really
 * is "what have I ordered".
 *
 * Deliberately no advance, refund or cancel controls. Those live in the console,
 * which is served from the farm's own server (ADR-0016) and never reaches a
 * customer's browser — so they are absent rather than hidden behind a check.
 */

export function CabinetPage({
  locale,
  open,
  onOpen,
  onConfigure,
}: {
  locale: Locale
  /** The order number in the address bar, or `null` for "the newest one". */
  open: string | null
  onOpen: (number: string | null) => void
  /** «Повторить заказ» — the configurator, with the model already chosen. */
  onConfigure: (asset: { id: string; name: string } | null) => void
}) {
  const { actor, ready } = useSession()
  /*
    Both of these are *tagged* with what they were fetched for, and the value the
    screen renders is derived from that tag rather than cleared by an effect.

    Clearing in an effect is what this used to do, and it works only after a
    render has already happened — so the previous order's machine and percentage
    appeared under the new order's number for one frame, which is the one thing a
    tracking screen must never do. Deriving means there is no such frame: the
    moment `current` changes, the tag stops matching and the panel reads empty.
    `react-hooks/set-state-in-effect` is what pointed at it.
  */
  const [fetchedOrders, setFetchedOrders] = useState<{ actor: string; rows: Order[] } | null>(
    null,
  )
  const [fetchedProgress, setFetchedProgress] = useState<{ order: string; value: Progress } | null>(
    null,
  )
  const [colourNames, setColourNames] = useState<Record<string, string>>({})

  const orders = actor && fetchedOrders?.actor === actor.user_id ? fetchedOrders.rows : null

  useEffect(() => {
    if (!actor) return
    const mine = actor.user_id
    let live = true
    void api
      .get<{ rows: Order[] }>('/orders/mine?limit=200')
      .then((page) => live && setFetchedOrders({ actor: mine, rows: page.rows }))
      .catch(() => live && setFetchedOrders({ actor: mine, rows: [] }))
    /*
      The palette, so the composition panel can name the colours rather than
      print six hex codes. A miss falls back to the hex, which is what the order
      actually stores — a colour the shop has stopped carrying still renders.
    */
    void api
      .get<{ rows: { color_hex: string; color_name: string }[] }>('/materials')
      .then((table) => {
        if (!live) return
        setColourNames(
          Object.fromEntries(
            table.rows
              .filter((row) => row.color_hex && row.color_name)
              .map((row) => [row.color_hex.toLowerCase(), row.color_name]),
          ),
        )
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [actor])

  /*
    The newest order is the one somebody arriving without a number wants: it is
    the one they just placed, and it is the one still moving.
  */
  const current = orders?.find((row) => row.number === open) ?? orders?.[0] ?? null

  const progress = current && fetchedProgress?.order === current.id ? fetchedProgress.value : null

  useEffect(() => {
    if (!current) return
    const shown = current.id
    let live = true
    void api
      .get<Progress>(`/orders/${shown}/queue`)
      .then((body) => live && setFetchedProgress({ order: shown, value: body }))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [current])

  /*
    The kit's strip here is `ACCOUNT · TIER · ORDERS`, and the first two belong
    to the account screen — this one tracks an order, so it names the order.
    `ЭТАП` repeats the pipeline's own counter deliberately: the strip is what
    somebody quotes down a telephone, and «этап 5 из 9» is the sentence they say.
  */
  useChrome(
    current
      ? {
          meta: [
            { label: 'ЗАКАЗ', value: current.number },
            {
              label: 'ЭТАП',
              value: `${pipeline(current, progress).filter((s) => s.state === 'done' || s.state === 'now').length} / 9`,
            },
            { label: 'ЗАКАЗОВ', value: String(orders?.length ?? 0) },
          ],
        }
      : null,
  )

  if (!ready) return <p className="hv-hint">{translate(locale, 'common.loading')}</p>

  if (!actor) {
    return (
      <div className="hv-cols hv-cols--2">
        <section className="hv-frame hv-frame--wide">
          <span className="hv-label">{translate(locale, 'cabinet.title')}</span>
          <h1 className="hv-h hv-h--lead" style={{ marginTop: 'var(--hv-1)' }}>
            Войдите, чтобы увидеть свои заказы
          </h1>
          <p className="hv-prose" style={{ fontSize: 'var(--hv-size-small)' }}>
            Девять этапов от оплаты до отправки, место в очереди и обещанный срок. Если
            производство выйдет за срок, цена снизится автоматически.
          </p>
        </section>
        <AuthPanel locale={locale} />
      </div>
    )
  }

  if (orders === null) return <p className="hv-hint">{translate(locale, 'common.loading')}</p>

  if (orders.length === 0) {
    return (
      <section className="hv-frame hv-frame--wide">
        <span className="hv-label">{translate(locale, 'cabinet.title')}</span>
        <h1 className="hv-h hv-h--lead" style={{ marginTop: 'var(--hv-1)' }}>
          Заказов пока нет
        </h1>
        <p className="hv-prose" style={{ fontSize: 'var(--hv-size-small)' }}>
          Загрузите модель в конфигуратор — цена считается сразу и построчно, до того как
          вы что-либо подтвердите.
        </p>
        <button
          className="hv-btn hv-btn--primary"
          type="button"
          style={{ marginTop: 'var(--hv-3)' }}
          onClick={() => onConfigure(null)}
        >
          Рассчитать заказ
        </button>
      </section>
    )
  }

  if (!current) return <p className="hv-hint">{translate(locale, 'common.loading')}</p>

  const line = current.lines[0]
  const archived = orders.filter((row) => CLOSED.includes(row.status)).length

  return (
    <div className="hv-cols hv-cols--3r">
      <aside className="hv-panel">
        <div className="hv-panel__head">
          <span>Заказы</span>
          <span className="hv-panel__aside">{orders.length}</span>
        </div>
        <nav className="hv-nav" aria-label="Мои заказы">
          {orders.map((row) => (
            <button
              key={row.id}
              className="hv-nav__item"
              type="button"
              aria-current={row.number === current.number ? 'page' : undefined}
              onClick={() => onOpen(row.number)}
            >
              <span className="hv-nav__lead">
                <span>{row.number}</span>
                {/* The kit's dot marks work still in hand, on the current row
                    and on any other still moving. */}
                {!CLOSED.includes(row.status) && <span className="hv-nav__dot" />}
              </span>
              <span className="hv-nav__chev">›</span>
            </button>
          ))}
        </nav>
        <div className="hv-panel__foot">
          <span>АРХИВ :: {archived} ЗАВЕРШЁННЫХ</span>
        </div>
      </aside>

      <div className="hv-stack">
        <div className="hv-frame hv-frame--wide">
          <div className="hv-row hv-row--between">
            <div>
              <h1 className="hv-display" style={{ fontSize: 'clamp(1.8rem,5vw,3.4rem)' }}>
                {current.number}
              </h1>
              <p className="hv-micro" style={{ margin: 'var(--hv-2) 0 0' }}>
                {[
                  line?.model_name.toUpperCase(),
                  line?.material_code.toUpperCase(),
                  line && line.colors.length > 1 ? `${line.colors.length} ЦВЕТА` : null,
                  line ? `${line.quantity} ШТ` : null,
                  `СОЗДАН ${stamp(current.created_at, locale)}`,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            </div>
            <div className="hv-right">
              <span className="hv-state" data-state={stateOf(current.status)}>
                {translate(locale, `order.status.${current.status}` as never)}
              </span>
              <div className="hv-micro" style={{ marginTop: 'var(--hv-1)' }}>
                ОБЕЩАННЫЙ СРОК :: {current.promised_at ? fullStamp(current.promised_at, locale) : NONE}
              </div>
            </div>
          </div>
        </div>

        <CabinetPipeline locale={locale} order={current} progress={progress} />
        <CabinetSummary locale={locale} order={current} colourNames={colourNames} />
        <CabinetHistory locale={locale} order={current} />
      </div>

      <CabinetAside
        locale={locale}
        order={current}
        progress={progress}
        others={orders.filter((row) => row.number !== current.number).slice(0, 3)}
        onOpen={onOpen}
        onRepeat={() =>
          onConfigure(
            line?.model_asset_id
              ? { id: line.model_asset_id, name: line.model_name }
              : // Ordered before the asset id was carried on a line, or from a
                // file the farm has since collected. The configurator opens
                // empty rather than on somebody else's geometry.
                null,
          )
        }
      />
    </div>
  )
}
