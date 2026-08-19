import type { Locale } from '@printorian/ui'
import { translate } from '@printorian/ui'

import { HALTED, NONE, duration, pipeline, stamp } from './cabinet'
import type { Machine, Order, Progress } from './cabinet'

/**
 * «Ход выполнения» — the scenario's steps 5–10 as the kit draws them.
 *
 * Nine stages, each dated from an event the order actually has. Two of them —
 * postprocessing and quality control — are stages nothing on this farm advances
 * yet: they exist in the state machine and no context writes them, so an order
 * goes from printing to packing and passes *over* them. The pipeline says
 * «НЕ ОТМЕЧЕН» there rather than «—», because an em dash after the order has
 * shipped reads as "still to come".
 *
 * The progress bar and the machine footer come from the printer's own last
 * report, never from an estimate. A machine that has not reported has no
 * progress rather than nought progress, and the bar is absent rather than empty
 * (ADR-0007 — a driver never invents a figure, and neither does the screen that
 * renders one).
 */
export function CabinetPipeline({
  locale,
  order,
  progress,
}: {
  locale: Locale
  order: Order
  progress: Progress | null
}) {
  const stages = pipeline(order, progress)
  const halted = HALTED.includes(order.status)
  const done = stages.filter((stage) => stage.state === 'done' || stage.state === 'now').length
  const machine = progress?.machine ?? null
  const percent = machine?.progress_percent ?? progress?.queue?.progress_percent ?? null

  /*
    What the *current* stage shows instead of the moment it began.

    The kit writes «63% · ост. 7 ч 26 м» there, and it is right to: the stage
    that is happening is the one whose start time matters least — the reader can
    see it is happening — and the one where the live figure has nowhere else to
    go on a narrow column. Every other stage keeps its timestamp, which is the
    fact it exists to record.

    Absent unless the machine has actually reported. A stage that fell back to
    «0% · ост. —» would be worse than the timestamp it replaced.
  */
  const live =
    percent !== null || machine?.remaining_minutes != null
      ? [
          percent !== null ? `${percent}%` : null,
          machine?.remaining_minutes != null
            ? `ост. ${duration(machine.remaining_minutes, locale)}`
            : null,
        ]
          .filter(Boolean)
          .join(' · ')
      : null

  return (
    <section className="hv-panel">
      <div className="hv-panel__head">
        <span>Ход выполнения</span>
        <span className="hv-panel__aside">
          {halted
            ? translate(locale, `order.status.${order.status}` as never).toUpperCase()
            : `ЭТАП ${done} ИЗ ${stages.length}`}
        </span>
      </div>

      <div className="hv-pipe">
        {stages.map((stage, index) => (
          <div
            key={stage.key}
            className="hv-pipe__step"
            // `now` only while the order is still moving. A cancelled order's
            // last stage is not in progress, and pulsing it says it is.
            data-state={halted && stage.state === 'now' ? 'done' : stage.state === 'skipped' ? 'done' : stage.state}
          >
            <div className="hv-pipe__n">{String(index + 1).padStart(2, '0')}</div>
            <div className="hv-pipe__k">
              {stage.key === 'assigned' && progress?.machine
                ? `Назначен ${progress.machine.name}`
                : stage.label}
            </div>
            <div className="hv-pipe__t">
              {stage.state === 'now' && !halted && live
                ? live
                : stage.at !== null
                  ? stamp(stage.at, locale)
                  : stage.state === 'skipped'
                    ? 'НЕ ОТМЕЧЕН'
                    : NONE}
            </div>
          </div>
        ))}
      </div>

      {percent !== null && !halted && (
        <div className="hv-panel__body hv-panel__body--tight">
          <div className="hv-meter-row">
            <div className="hv-meter" data-tone="live">
              <div className="hv-meter__fill" style={{ width: `${percent}%` }} />
            </div>
            <span className="hv-meter-row__pct">{percent}%</span>
          </div>
        </div>
      )}

      <MachineFoot locale={locale} machine={machine} attempt={progress?.queue?.attempt ?? 1} />
    </section>
  )
}

/**
 * «ПРИНТЕР :: P-01 · BAMBU X1C» and the layer counter beside it.
 *
 * The kit's third clause is the rack the machine stands in. It is not here: the
 * console is on the farm's own network so its internals stay off the internet
 * (ADR-0016), and a storefront that publishes the floor plan one order at a time
 * gives that away by instalments. Which machine and which model is the
 * customer's business — it is their part on it.
 */
function MachineFoot({
  locale,
  machine,
  attempt,
}: {
  locale: Locale
  machine: Machine | null
  attempt: number
}) {
  if (!machine) {
    return (
      <div className="hv-panel__foot">
        <span>ПРИНТЕР ЕЩЁ НЕ НАЗНАЧЕН</span>
        {attempt > 1 && <span className="hv-warn">ПЕЧАТЬ №{attempt} · ЗА СЧЁТ ФЕРМЫ</span>}
      </div>
    )
  }

  const layers =
    machine.layer_current !== null && machine.layer_total
      ? `СЛОЙ ${machine.layer_current} / ${machine.layer_total}`
      : machine.remaining_minutes !== null
        ? `ОСТАЛОСЬ ${duration(machine.remaining_minutes, locale).toUpperCase()}`
        : // Neither reported. Saying nothing beats saying nought.
          'МАШИНА ЕЩЁ НЕ ОТЧИТАЛАСЬ'

  return (
    <div className="hv-panel__foot">
      <span>
        ПРИНТЕР :: {machine.name.toUpperCase()}
        {machine.model && ` · ${machine.brand.toUpperCase()} ${machine.model.toUpperCase()}`}
      </span>
      <span>{attempt > 1 ? `ПЕЧАТЬ №${attempt} · ЗА СЧЁТ ФЕРМЫ` : layers}</span>
    </div>
  )
}
