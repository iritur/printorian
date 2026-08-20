import type { Locale } from '@printorian/ui'
import { formatMoney, translate } from '@printorian/ui'

import { NONE, plural, stamp } from './cabinet'
import type { Order, OrderEvent } from './cabinet'

/**
 * «История заказа» — every recorded transition, newest first.
 *
 * The event log the order already carries, rendered rather than summarised.
 * This is the screen's receipt: the pipeline above says where the order *is*,
 * and this says how it got there and who moved it, so a customer arguing about
 * a date has the same record the farm does.
 *
 * The kit's third column is «Кто / что» and names a machine, the scheduler,
 * the slicer, the gateway. **This one is «Причина», because the farm does not
 * record an actor name.** `OrderEvent` carries an `actor_id` — a user id, which
 * is useless to a customer and is somebody's identity besides — and nothing
 * else. Filling the column with «Планировщик» on every row would look like the
 * kit and mean nothing at all.
 *
 * What it shows instead is real: the reason recorded with the transition, when
 * somebody gave one. The default reason is the status again (`order.printing`),
 * which the status column already says, so those rows are «—» rather than a
 * code printed twice.
 */
export function CabinetHistory({ locale, order }: { locale: Locale; order: Order }) {
  // Newest first: somebody opening this wants the last thing that happened, and
  // the log is written in the order events occurred.
  const events = [...order.events].sort((left, right) => right.sequence - left.sequence)

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>История заказа</span>
        <span className="hv-panel__aside">
          {events.length} {plural(events.length, 'СОБЫТИЕ', 'СОБЫТИЯ', 'СОБЫТИЙ')}
        </span>
      </div>
      <div className="hv-panel__body--none">
        <div className="hv-table-wrap">
          <table className="hv-table">
            <thead>
              <tr>
                <th>Время</th>
                <th>Событие</th>
                <th>Причина</th>
                <th data-align="end">Изменение</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.sequence}>
                  <td className="hv-table__id">{stamp(event.created_at, locale)}</td>
                  <td>{translate(locale, `order.status.${event.to_status}` as never)}</td>
                  <td className={why(event) === NONE ? 'hv-faint' : undefined}>{why(event)}</td>
                  <td data-align="end" className={change(event) === null ? 'hv-faint' : undefined}>
                    {change(event) === null
                      ? NONE
                      : formatMoney(change(event) as string, order.currency, locale)}
                  </td>
                </tr>
              ))}
              {events.length === 0 && (
                <tr>
                  <td colSpan={4} className="hv-faint">
                    {translate(locale, 'common.empty')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}

/**
 * The reason recorded with a transition, when it says more than the status does.
 *
 * `advance` defaults the reason to `order.<status>`, which the status column
 * already renders in words — so the default is suppressed here rather than
 * printed as a raw code beside its own translation. A reason somebody actually
 * typed is shown as typed.
 */
function why(event: OrderEvent): string {
  const by = event.details?.['by'] ?? event.details?.['actor'] ?? event.details?.['printer']
  if (typeof by === 'string' && by) return by
  if (!event.reason || event.reason === `order.${event.to_status}`) return NONE
  return event.reason
}

/** A money figure the event recorded, or `null` when it moved no money. */
function change(event: OrderEvent): string | null {
  const amount = event.details?.['amount']
  return typeof amount === 'string' || typeof amount === 'number' ? String(amount) : null
}
