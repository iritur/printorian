import type { Locale } from '@printorian/ui'
import { formatMoney, lineLabel, translate } from '@printorian/ui'

import { HALTED, NONE, stamp } from './cabinet'
import type { Order } from './cabinet'

/**
 * «Состав заказа» and «Оплата», side by side as the kit has them.
 *
 * Both read the order and nothing else. The composition is the *pinned* line —
 * what was configured, in the words it was configured in — and the payment is
 * the *pinned* breakdown, never recomputed (ADR-0002). A year later this panel
 * still shows what was agreed, under whatever rates apply by then.
 */

/** Discount codes, which the kit shows as their own credited rows. */
const DISCOUNTS = ['adjustment.volume_discount', 'adjustment.customer_discount']

export function CabinetSummary({
  locale,
  order,
  colourNames,
}: {
  locale: Locale
  order: Order
  /** Hex to the palette's own name, from the material catalogue. */
  colourNames: Record<string, string>
}) {
  const line = order.lines[0]
  const credits = (order.price_breakdown?.lines ?? []).filter((row) =>
    DISCOUNTS.includes(row.code),
  )
  const late = Number(order.sla_credit) > 0
  // A cancelled order owes nothing and is owed nothing. Showing its total
  // under «К доплате» would be a bill for work the farm is not doing.
  const halted = HALTED.includes(order.status)

  return (
    <div className="hv-cols hv-cols--2">
      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>Состав заказа</span>
        </div>
        <div className="hv-panel__body hv-panel__body--tight">
          <ul className="hv-leaders">
            <Leader k="Модель" v={line?.model_name ?? NONE} />
            <Leader k="Материал" v={line ? line.material_code.toUpperCase() : NONE} />
            <li className="hv-leader">
              <span className="hv-leader__k">Цвета</span>
              <span className="hv-leader__fill" />
              <span className="hv-leader__v">
                {line && line.colors.length > 0 ? (
                  <Colours colours={line.colors} names={colourNames} />
                ) : (
                  NONE
                )}
              </span>
            </li>
            <Leader
              k="Масштаб"
              v={line ? `${Math.round(Number(line.scale) * 100)}%` : NONE}
            />
            <Leader
              k="Обработка"
              v={
                line && line.finishes.length > 0
                  ? line.finishes
                      // The same catalogue key the configurator's own finish
                      // buttons use, so an order reads back in the words it
                      // was placed in.
                      .map((code) => translate(locale, `postprocess.${code}` as never))
                      .join(' · ')
                  : 'Без обработки'
              }
            />
            <Leader k="Количество" v={line ? `${line.quantity} шт` : NONE} />
            {line?.rush && <Leader k="Срочно" v="Да" tone="warn" />}
          </ul>
        </div>
        <div className="hv-panel__foot">
          <span>
            {order.delivery_method === 'pickup'
              ? 'САМОВЫВОЗ С ФЕРМЫ'
              : [order.delivery_postcode, order.delivery_city, order.delivery_address]
                  .filter(Boolean)
                  .join(', ')
                  .toUpperCase() || 'АДРЕС НЕ УКАЗАН'}
          </span>
        </div>
      </section>

      <section className="hv-panel">
        <div className="hv-panel__head">
          <span>Оплата</span>
        </div>
        <div className="hv-panel__body hv-panel__body--tight">
          <ul className="hv-leaders">
            {/*
              The credited rows are the breakdown's own, at the breakdown's own
              amounts — already negative, printed as they are stored. The client
              does no arithmetic on money anywhere, so there is no reconstructed
              "before discount" figure here: the engine's total is the total.
            */}
            {credits.map((row) => (
              <li key={row.code} className="hv-leader" data-tone="good">
                <span className="hv-leader__k">{lineLabel(row.code, locale)}</span>
                <span className="hv-leader__fill" />
                <span className="hv-leader__v">
                  {formatMoney(row.amount, order.currency, locale)}
                </span>
              </li>
            ))}
            <Leader
              k={order.paid_at ? `Оплачено ${stamp(order.paid_at, locale)}` : 'Сумма заказа'}
              v={formatMoney(order.total, order.currency, locale)}
            />
            {late && (
              <Leader
                k="Скидка за просрочку"
                v={`− ${formatMoney(order.sla_credit, order.currency, locale)}`}
                tone="good"
              />
            )}
          </ul>
          <hr className="hv-hr" />
          <div className="hv-slab">
            <span>{halted ? 'Итог' : order.paid_at && late ? 'К возврату' : 'К доплате'}</span>
            {/*
              `payable_now` is computed on the server. The alternative was the
              browser subtracting one decimal string from another, and a
              JavaScript number cannot hold money exactly.
            */}
            <span className="hv-slab__v">
              {halted
                ? formatMoney('0', order.currency, locale)
                : order.paid_at
                  ? formatMoney(late ? order.sla_credit : '0', order.currency, locale)
                  : formatMoney(order.payable_now, order.currency, locale)}
            </span>
          </div>
          <p className="hv-micro" style={{ margin: 'var(--hv-2) 0 0' }}>
            {halted
              ? 'ЗАКАЗ ОСТАНОВЛЕН · ОПЛАЧЕННОЕ ВОЗВРАЩАЕТСЯ ТЕМ ЖЕ СПОСОБОМ'
              : 'ЕСЛИ МЫ ВЫЙДЕМ ЗА ОБЕЩАННЫЙ СРОК, ЧАСТЬ СУММЫ ВЕРНЁТСЯ АВТОМАТИЧЕСКИ'}
          </p>
        </div>
      </section>
    </div>
  )
}

function Leader({ k, v, tone }: { k: string; v: string; tone?: 'good' | 'warn' | 'bad' }) {
  return (
    <li className="hv-leader" {...(tone ? { 'data-tone': tone } : {})}>
      <span className="hv-leader__k">{k}</span>
      <span className="hv-leader__fill" />
      <span className="hv-leader__v">{v}</span>
    </li>
  )
}

/**
 * The plate's colours, named where the catalogue knows the name.
 *
 * The order stores hex, because that is what the AMS reports and what the
 * configurator chose — so a colour the shop has since stopped carrying still
 * renders correctly here rather than becoming a dangling code. The name is a
 * lookup and falls back to the hex, which is the fact.
 */
function Colours({
  colours,
  names,
}: {
  colours: string[]
  names: Record<string, string>
}) {
  return (
    <span className="hv-row" style={{ gap: 'var(--hv-1)', justifyContent: 'flex-end' }}>
      {colours.map((hex, index) => (
        <span key={`${hex}-${index}`} className="hv-row" style={{ gap: 4 }}>
          <i
            aria-hidden="true"
            style={{
              display: 'inline-block',
              width: 10,
              height: 10,
              background: hex,
              border: '1px solid var(--hv-line)',
            }}
          />
          {names[hex.toLowerCase()] ?? hex.toUpperCase()}
        </span>
      ))}
    </span>
  )
}
