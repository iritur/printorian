import { translate } from '@printorian/ui'
import type { Locale } from '@printorian/ui'

/**
 * «02 :: Доставка» and «03 :: Оплата» — the two things checkout asks that the
 * configurator did not.
 *
 * Split from the page because the page is about placing and paying for an order
 * and this is about collecting the answers, and together they were past the
 * length this project holds itself to.
 */

/** Matches `contexts/ordering/policies.py::DeliveryMethod`. */
export type DeliveryMethod = 'pickup' | 'courier' | 'freight'

export interface Delivery {
  method: DeliveryMethod
  city: string
  postcode: string
  address: string
  notify: boolean
}

export const EMPTY_DELIVERY: Delivery = {
  // Collection, because it is the only choice that needs nothing else answered
  // and the only one that cannot be wrong. Defaulting to courier would put an
  // address requirement in front of somebody who was going to drive over.
  method: 'pickup',
  city: '',
  postcode: '',
  address: '',
  notify: true,
}

/** Whether the farm has to get this order to an address. Mirrors `is_shipped`. */
export const isShipped = (method: DeliveryMethod) => method !== 'pickup'

/**
 * Whether the delivery half is answered well enough to place the order.
 *
 * The same rule the server enforces, not a stricter one: an address is required
 * for anything that ships and meaningless for collection. Checked here so the
 * customer is told before the round trip, and there so a client cannot skip it.
 */
export function deliveryReady(delivery: Delivery): boolean {
  return !isShipped(delivery.method) || Boolean(delivery.city.trim() && delivery.address.trim())
}

/**
 * How the money arrives, as a gateway rather than as a card scheme.
 *
 * The kit draws three options — card, SBP, invoice. This offers two, and the
 * difference is worth stating: card and SBP are both chosen on the acquirer's own
 * page, so sending the identical request under two labels would be theatre. What
 * the system genuinely distinguishes is *paying now through a gateway* against
 * *being invoiced and settled by an operator*, which is a real fork with a real
 * consequence for when the order starts.
 */
export type PayWith = 'online' | 'invoice'

export function DeliveryStep({
  locale,
  value,
  onChange,
}: {
  locale: Locale
  value: Delivery
  onChange: (delivery: Delivery) => void
}) {
  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)
  const methods: [DeliveryMethod, string][] = [
    ['pickup', t('checkout.pickup')],
    ['courier', t('checkout.courier')],
    ['freight', t('checkout.freight')],
  ]

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>02 :: {t('checkout.delivery')}</span>
        <span className="hv-panel__aside">{t('checkout.delivery_affects')}</span>
      </div>
      <div className="hv-panel__body hv-stack">
        <div className="hv-seg" role="group" aria-label={t('checkout.delivery')}>
          {methods.map(([method, label]) => (
            <button
              key={method}
              type="button"
              className="hv-seg__btn"
              aria-pressed={value.method === method}
              onClick={() => onChange({ ...value, method })}
            >
              {label}
            </button>
          ))}
        </div>

        {/*
          The address fields appear only for an order that ships. Collection needs
          none of them, and the server refuses an address on neither — asking for
          one anyway would be three fields the customer has to work out are
          optional.
        */}
        {isShipped(value.method) ? (
          <>
            <div className="hv-grid hv-grid--2">
              <div className="hv-field">
                <label className="hv-label" htmlFor="co-city">
                  {t('checkout.city')}
                </label>
                <input
                  className="hv-input"
                  id="co-city"
                  required
                  value={value.city}
                  onChange={(event) => onChange({ ...value, city: event.target.value })}
                />
              </div>
              <div className="hv-field">
                <label className="hv-label" htmlFor="co-postcode">
                  {t('checkout.postcode')}
                </label>
                <input
                  className="hv-input"
                  id="co-postcode"
                  inputMode="numeric"
                  value={value.postcode}
                  onChange={(event) => onChange({ ...value, postcode: event.target.value })}
                />
              </div>
            </div>
            <div className="hv-field">
              <label className="hv-label" htmlFor="co-address">
                {t('checkout.address')}
              </label>
              <input
                className="hv-input"
                id="co-address"
                required
                placeholder={t('checkout.address_placeholder')}
                value={value.address}
                onChange={(event) => onChange({ ...value, address: event.target.value })}
              />
            </div>
          </>
        ) : (
          /*
            Said out loud rather than left to inference. Collection is the one
            choice that removes a line from the price, and a customer who does not
            know that cannot weigh it.
          */
          <p className="hv-hint">{t('checkout.pickup_hint')}</p>
        )}

        <label className="hv-check">
          <input
            type="checkbox"
            checked={value.notify}
            onChange={(event) => onChange({ ...value, notify: event.target.checked })}
          />
          <span className="hv-check__body">
            <span className="hv-h">{t('checkout.notify')}</span>
            <span className="hv-hint">{t('checkout.notify_hint')}</span>
          </span>
        </label>
      </div>
    </section>
  )
}

export function PaymentStep({
  locale,
  value,
  onChange,
}: {
  locale: Locale
  value: PayWith
  onChange: (choice: PayWith) => void
}) {
  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)
  const options: [PayWith, string, string][] = [
    ['online', t('checkout.pay_online'), t('checkout.pay_online_note')],
    ['invoice', t('checkout.pay_invoice'), t('checkout.pay_invoice_note')],
  ]

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>03 :: {t('checkout.payment')}</span>
      </div>
      <div className="hv-panel__body hv-stack hv-stack--2">
        {options.map(([choice, label, note]) => (
          <button
            key={choice}
            type="button"
            className="hv-option"
            aria-pressed={value === choice}
            onClick={() => onChange(choice)}
          >
            <span className="hv-h">{label}</span>
            <span className="hv-micro">{note}</span>
          </button>
        ))}
        <p className="hv-micro" style={{ margin: 'var(--hv-2) 0 0' }}>
          {t('checkout.pay_disclaimer')}
        </p>
      </div>
    </section>
  )
}
