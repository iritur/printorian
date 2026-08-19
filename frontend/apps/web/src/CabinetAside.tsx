import type { Locale, MessageKey } from '@printorian/ui'
import { formatMoney, translate } from '@printorian/ui'

import { HALTED, NONE, duration, overdueHours, shortWhen, stamp, stateOf } from './cabinet'
import type { Order, Progress } from './cabinet'

/**
 * The kit's right-hand column: the queue, the neighbours, and the late clause.
 *
 * The queue frame is the one place on this screen where the honesty rule from
 * the planner reaches the person waiting: work queueing for a *machine* gets a
 * place and a predicted start; work blocked on a *person* gets a reason and no
 * date at all. A comfortable estimate for the second kind is how a customer is
 * quietly misled, and it is the queue's version of inventing telemetry.
 */
export function CabinetAside({
  locale,
  order,
  progress,
  others,
  onOpen,
  onRepeat,
}: {
  locale: Locale
  order: Order
  progress: Progress | null
  others: Order[]
  onOpen: (number: string) => void
  onRepeat: () => void
}) {
  return (
    <aside className="hv-sticky hv-stack">
      <QueueFrame locale={locale} order={order} progress={progress} />

      {others.length > 0 && (
        <section className="hv-panel">
          <div className="hv-panel__head">
            <span>Другие заказы</span>
          </div>
          <div className="hv-panel__body--none">
            <table className="hv-table">
              <tbody>
                {others.map((row) => (
                  <tr key={row.id} data-activatable onClick={() => onOpen(row.number)}>
                    <td>
                      {row.number}
                      <div className="hv-micro">
                        {row.lines[0]
                          ? `${row.lines[0].model_name.toUpperCase()} · ${row.lines[0].quantity} ШТ`
                          : NONE}
                      </div>
                    </td>
                    <td data-align="end">
                      <span className="hv-state" data-state={stateOf(row.status)}>
                        {translate(locale, `order.status.${row.status}` as never)}
                      </span>
                      <div className="hv-micro">{stamp(row.created_at, locale)}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <LateClause locale={locale} order={order} />

      <button className="hv-btn hv-btn--block" type="button" onClick={onRepeat}>
        Повторить заказ
      </button>
    </aside>
  )
}

function QueueFrame({
  locale,
  order,
  progress,
}: {
  locale: Locale
  order: Order
  progress: Progress | null
}) {
  const queue = progress?.queue ?? null
  const machine = progress?.machine ?? null
  const finished = ['shipped', 'completed'].includes(order.status)

  /*
    The headline, in the order the customer would ask the question. Finished
    first, because an order that has left needs no queue; then printing, which
    is the state the frame's live styling exists for; then waiting, which splits
    into "a machine will free up" and "somebody has to do something" — the split
    the whole panel exists to keep visible.
  */
  const headline = finished
    ? 'ЗАВЕРШЁН'
    : HALTED.includes(order.status)
      ? 'ОСТАНОВЛЕН'
      : queue === null
        ? 'ПРИНЯТ'
        : queue.job_status === 'printing'
          ? 'В РАБОТЕ'
          : queue.position !== null
            ? `МЕСТО ${queue.position}`
            : 'ОЖИДАНИЕ'

  const note = finished
    ? 'РАБОТА ОКОНЧЕНА'
    : queue === null
      ? 'ЗАДАНИЕ ЕЩЁ НЕ СОЗДАНО'
      : queue.job_status === 'printing'
        ? 'ЗАКАЗ УЖЕ НА ПРИНТЕРЕ — ОЖИДАНИЕ ЗАВЕРШЕНО'
        : queue.reason
          ? translate(locale, `queue.${queue.reason}` as MessageKey).toUpperCase()
          : 'ОЖИДАЕТ ОЧЕРЕДИ'

  /*
    The live accent is the panel's, not the order's.

    It used to be conditional — cyan while printing, plain otherwise — and that
    was wrong twice over. The kit draws «Очередь» in the live colour full stop,
    and the *state* is already said three times on this screen anyway: in the
    headline below, in the pulsing pipeline step, and in the status badge at the
    top. Colouring the frame by status made the same statement a fourth time and
    left the column looking broken on every order that was not mid-print.
  */
  return (
    <section className="hv-frame hv-frame--live">
      <span className="hv-h hv-live">Очередь</span>
      <div className="hv-stat" style={{ padding: 'var(--hv-3) 0' }}>
        <span className="hv-stat__v">{headline}</span>
        <span className="hv-micro">{note}</span>
      </div>
      <hr className="hv-hr" />
      {/* The kit's three rows, in the kit's order. */}
      <ul className="hv-leaders">
        <li className="hv-leader">
          <span className="hv-leader__k">Осталось печати</span>
          <span className="hv-leader__fill" />
          {/*
            The machine's own figure, not a countdown derived from an estimate.
            A printer that has not reported has no remaining time rather than
            nought, and the dash says so.
          */}
          <span className="hv-leader__v">{duration(machine?.remaining_minutes ?? null, locale)}</span>
        </li>
        <li className="hv-leader">
          <span className="hv-leader__k">Постобработка</span>
          <span className="hv-leader__fill" />
          {/*
            The kit prints «~ 2 ч» here and the farm cannot. Nothing tracks
            postprocessing — it is one of the two pipeline stages no context
            advances — so there is no duration to quote and no measurement to
            derive one from. The row keeps its place, because it is part of the
            sum the row below states, and it fills in the day that context
            lands rather than the day somebody picks a plausible number.
          */}
          <span className="hv-leader__v">{NONE}</span>
        </li>
        <li className="hv-leader">
          <span className="hv-leader__k">
            {queue?.predicted_start ? 'Начало печати' : 'Готовность'}
          </span>
          <span className="hv-leader__fill" />
          <span className="hv-leader__v">
            {queue?.predicted_start
              ? shortWhen(queue.predicted_start, locale)
              : shortWhen(machine?.eta ?? null, locale)}
          </span>
        </li>
      </ul>
      {queue !== null &&
        queue.position === null &&
        queue.predicted_start === null &&
        queue.job_status !== 'printing' && (
          <p className="hv-micro" style={{ margin: 'var(--hv-2) 0 0' }}>
            {/* The distinction the whole feature exists for. */}
            {translate(locale, 'queue.no_estimate').toUpperCase()}
          </p>
        )}
    </section>
  )
}

/**
 * «Скидка за просрочку» — the scenario's promise that lateness costs the farm.
 *
 * The hours are computed here because they change every minute and a
 * server-rendered figure would be stale on arrival. The **money** is not: it is
 * `sla_credit`, what the farm has actually accrued and will actually refund. A
 * client that worked out its own would eventually disagree with the payment,
 * and the customer would be right to believe the wrong one.
 */
function LateClause({ locale, order }: { locale: Locale; order: Order }) {
  const late = overdueHours(order)
  const credit = Number(order.sla_credit)
  // Lateness does not apply to an order nobody is working on, and «0 ч» there
  // reads as "delivered on time" rather than "this was called off".
  const halted = HALTED.includes(order.status)

  return (
    <section className="hv-frame" style={{ '--ink': 'var(--hv-warn)' } as React.CSSProperties}>
      <span className="hv-h hv-warn">Скидка за просрочку</span>
      <p className="hv-prose" style={{ fontSize: 'var(--hv-size-small)', marginTop: 'var(--hv-2)' }}>
        Если заказ выйдет за обещанный срок, цена снижается автоматически — вам не нужно
        ничего требовать. Возврат придёт тем же способом оплаты.
      </p>
      <ul className="hv-leaders">
        <li className="hv-leader" {...(late !== null ? { 'data-tone': 'warn' } : {})}>
          <span className="hv-leader__k">
            {halted ? 'Просрочка' : order.shipped_at ? 'Просрочка · итог' : 'Просрочка · сейчас'}
          </span>
          <span className="hv-leader__fill" />
          <span className="hv-leader__v">
            {halted || order.promised_at === null ? NONE : `${Math.floor(late ?? 0)} ч`}
          </span>
        </li>
        <li className="hv-leader" {...(credit > 0 ? { 'data-tone': 'good' } : {})}>
          <span className="hv-leader__k">Начислено</span>
          <span className="hv-leader__fill" />
          <span className="hv-leader__v">
            {formatMoney(order.sla_credit, order.currency, locale)}
          </span>
        </li>
      </ul>
    </section>
  )
}
