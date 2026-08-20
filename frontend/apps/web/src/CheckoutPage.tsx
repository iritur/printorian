import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '@printorian/api-client'
import type { Breakdown, Locale } from '@printorian/ui'
import {
  AuthPanel,
  PriceBreakdown,
  api,
  formatMoney,
  snapshotLabel,
  translate,
  translateError,
  useChrome,
  useSession,
} from '@printorian/ui'

import { DeliveryStep, EMPTY_DELIVERY, PaymentStep, deliveryReady } from './CheckoutForm'
import type { Delivery, PayWith } from './CheckoutForm'
import type { Config } from './config'

export interface QuotedModel {
  fileName: string
  estimated_minutes: string
  estimated_grams: string
  promised_hours: string
}

export interface OrderView {
  id: string
  number: string
  status: string
  total: string
  sla_credit: string
  currency: string
  promised_at: string | null
  price_breakdown: Breakdown
}

interface PaymentView {
  id: string
  status: string
  amount: string
  confirmation_url: string | null
}

/**
 * Review, place, pay — the kit's three numbered panels and the order beside them.
 *
 * The price here is the one the configurator already quoted, and the order the
 * server creates is priced by the same engine from the same configuration. This
 * screen never invents a number and never negotiates one: it shows what was
 * agreed, collects the two things the configurator could not ask (where it goes
 * and how it is paid for), and takes the money.
 */
export function CheckoutPage({
  locale,
  config,
  model,
  breakdown,
  materialCode,
  colors,
  onBack,
  onDone,
}: {
  locale: Locale
  config: Config
  model: QuotedModel
  breakdown: Breakdown
  /** Resolved by the configurator, which holds the material catalogue. */
  materialCode: string
  colors: string[]
  onBack: () => void
  onDone: (order: OrderView) => void
}) {
  const { actor, ready } = useSession()
  const [delivery, setDelivery] = useState<Delivery>(EMPTY_DELIVERY)
  const [payWith, setPayWith] = useState<PayWith>('online')
  const [order, setOrder] = useState<OrderView | null>(null)
  /** The configurator's quote, corrected for the delivery chosen here. */
  const [repriced, setRepriced] = useState<Breakdown | null>(null)
  const [payment, setPayment] = useState<PaymentView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)

  /*
    The kit's third item here is `LOCK :: 23 Ч 41 М`, a countdown on a quote that
    expires. Nothing in this system expires — a breakdown is pinned to its order
    and never recomputed (ADR-0002) — so a timer would count down to an event
    that does not happen. The snapshot id is the fact underneath the kit's
    intent: it is what makes this exact price reproducible later.
  */
  useChrome({
    meta: [
      { label: 'MESH', value: model.fileName.toUpperCase() },
      { label: 'MATERIAL', value: materialCode.toUpperCase() },
      { label: 'RATES', value: snapshotLabel(breakdown.rate_snapshot_id) },
    ],
  })

  /** The order body, which is also exactly what re-pricing takes. */
  const asOrder = useCallback(
    (chosen: Delivery) => ({
      customer_email: actor?.email ?? '',
      promised_days: 5,
      delivery: chosen,
      lines: [
        {
          model_name: model.fileName,
          material_code: materialCode,
          quantity: config.quantity,
          scale: config.scale,
          rush: config.rush,
          colors,
          finishes: config.finishes,
          estimated_minutes: model.estimated_minutes,
          estimated_grams: model.estimated_grams,
        },
      ],
    }),
    [actor, config, colors, materialCode, model],
  )

  const fail = (exc: unknown) =>
    setError(
      exc instanceof ApiError
        ? translateError(locale, { code: exc.code, details: exc.details })
        : t('error.internal'),
    )

  /**
   * Re-price whenever the delivery choice changes.
   *
   * The configurator cannot ask where the parts are going, so it quotes with
   * delivery included. Without this the checkout would show that quote beside a
   * panel reading «Заберёте на ферме — доставка не считается», and the total
   * would silently drop by several hundred roubles the moment the customer
   * pressed the button. Same endpoint body as placing the order, and the same
   * spec builder behind it, so what is shown is what is charged.
   */
  useEffect(() => {
    if (order) return // Pinned. Nothing reprices an order that exists.
    let live = true
    void api
      /*
        The method alone, not the whole order. Pricing a courier delivery needs to
        know only that it is one — the rate is flat — and sending the order body
        would carry an address requirement that withholds the answer until the
        customer has typed one, which is exactly when they want to see it.
      */
      .post<{ breakdown: Breakdown }>('/orders/reprice', {
        method: delivery.method,
        lines: asOrder(delivery).lines,
      })
      .then((answer) => live && setRepriced(answer.breakdown))
      // A failed re-price leaves the configurator's quote on screen, which is
      // the safe direction: it includes delivery, so it can only overstate.
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [delivery, order, asOrder])

  const placeOrder = async () => {
    if (!actor) return
    setBusy(true)
    setError(null)
    try {
      // The configuration goes up; the price is computed server-side and pinned.
      const placed = await api.post<OrderView>('/orders', asOrder(delivery))
      setOrder(placed)
    } catch (exc: unknown) {
      fail(exc)
    } finally {
      setBusy(false)
    }
  }

  const startPayment = async () => {
    if (!order) return
    setBusy(true)
    setError(null)
    try {
      /*
        `manual` is the one gateway a customer chooses; anything else is the
        deployment's own, and the server picks it when the field is empty. A
        storefront naming a payment provider is a storefront that has to be
        redeployed when the farm changes acquirer.
      */
      const started = await api.post<PaymentView>('/payments', {
        order_id: order.id,
        ...(payWith === 'invoice' ? { provider: 'manual' } : {}),
      })
      setPayment(started)
    } catch (exc: unknown) {
      fail(exc)
    } finally {
      setBusy(false)
    }
  }

  if (!ready) return <p className="hv-hint">{t('common.loading')}</p>

  const priced = order?.price_breakdown ?? repriced ?? breakdown
  const canPlace = Boolean(actor) && deliveryReady(delivery) && !busy

  return (
    <div className="hv-cols hv-cols--2">
      <div className="hv-stack">
        <section className="hv-panel">
          <div className="hv-panel__head">
            <span>01 :: {t('checkout.account')}</span>
            <span className="hv-panel__aside">{t('checkout.account_required')}</span>
          </div>
          <div className="hv-panel__body">
            {actor ? (
              <div className="hv-row hv-row--between">
                <span className="hv-h">{actor.email}</span>
                <span className="hv-state" data-state="idle">
                  {t('checkout.sign_in')}
                </span>
              </div>
            ) : (
              <AuthPanel locale={locale} />
            )}
          </div>
        </section>

        <DeliveryStep locale={locale} value={delivery} onChange={setDelivery} />
        <PaymentStep locale={locale} value={payWith} onChange={setPayWith} />
      </div>

      <div className="hv-sticky hv-stack" aria-busy={busy}>
        <section className="hv-frame hv-frame--wide">
          <span className="hv-label">{t('checkout.order')}</span>
          <h1
            className="hv-display"
            style={{ fontSize: 'clamp(1.6rem,4vw,2.6rem)', marginTop: 'var(--hv-1)' }}
          >
            {model.fileName.replace(/\.[^.]+$/, '').toUpperCase()}
          </h1>
          <div className="hv-micro" style={{ marginTop: 'var(--hv-2)' }}>
            {describe(config, locale)}
          </div>
        </section>

        {error && (
          <p className="hv-hint hv-bad" role="alert">
            {error}
          </p>
        )}

        <PriceBreakdown
          breakdown={priced}
          locale={locale}
          promisedHours={model.promised_hours}
        />

        {/*
          One button at a time, because these are two different commitments. The
          order is created first — that is what pins the price — and only then is
          there something to pay for.
        */}
        {!order && (
          <button
            className="hv-btn hv-btn--primary hv-btn--lg hv-btn--block"
            type="button"
            disabled={!canPlace}
            onClick={() => void placeOrder()}
          >
            {t('checkout.place')}
          </button>
        )}

        {order && !payment && (
          <>
            <div className="hv-slab hv-slab--outline">
              <span>{t('checkout.placed')}</span>
              <span className="hv-slab__v hv-mono">{order.number}</span>
            </div>
            <button
              className="hv-btn hv-btn--primary hv-btn--lg hv-btn--block"
              type="button"
              disabled={busy}
              onClick={() => void startPayment()}
            >
              {t('checkout.pay')} · {formatMoney(order.total, order.currency, locale)}
            </button>
          </>
        )}

        {order && payment && (
          <>
            <div className="hv-slab hv-slab--outline">
              <span>{t('checkout.awaiting')}</span>
              <span className="hv-slab__v">
                {formatMoney(payment.amount, order.currency, locale)}
              </span>
            </div>
            {/*
              A real gateway sends the customer away to pay. Only rendered when
              there is somewhere to send them: the manual provider settles at the
              counter and has no page, and a dead button would suggest otherwise.
            */}
            {payment.confirmation_url && (
              <a
                className="hv-btn hv-btn--primary hv-btn--lg hv-btn--block"
                href={payment.confirmation_url}
                rel="noreferrer noopener"
              >
                {t('checkout.pay')}
              </a>
            )}
            <button
              className="hv-btn hv-btn--block"
              type="button"
              onClick={() => onDone(order)}
            >
              {t('cabinet.title')}
            </button>
          </>
        )}

        {/*
          Absent once the order exists: the configuration is pinned to it, so
          going "back to the configurator" would edit something the order no
          longer follows.
        */}
        {!order && (
          <button className="hv-btn hv-btn--block" type="button" onClick={onBack}>
            {t('checkout.back')}
          </button>
        )}

        <p className="hv-micro" style={{ textAlign: 'center', margin: 0 }}>
          {t('checkout.terms')}
        </p>
      </div>
    </div>
  )
}

/**
 * What was configured, in one line — the kit's «PETG-CF · ЧЁРНЫЙ + КРАСНЫЙ · 100%
 * · БЕЗ ОБРАБОТКИ · 10 ШТ».
 *
 * Assembled from the configuration rather than echoed from the server, because
 * this is the customer's own choices read back to them, and every part of it is
 * something they set on the previous screen. `config.colors` already holds the
 * names — the hex list beside it is what the *order* carries, and hex is not
 * something to read back to anybody.
 */
function describe(config: Config, locale: Locale): string {
  const finish = config.finishes[0] ?? 'raw'
  return [
    config.material,
    config.colors.join(' + ') || '—',
    `${Math.round(Number(config.scale) * 100)}%`,
    translate(locale, `postprocess.${finish}` as Parameters<typeof translate>[1]),
    config.rush ? translate(locale, 'adjustment.rush') : null,
    `${config.quantity} ${translate(locale, 'unit.piece')}`,
  ]
    .filter(Boolean)
    .join(' · ')
    .toUpperCase()
}
