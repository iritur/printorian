import type { Locale } from '@printorian/ui'

import { NONE, monogram, roubles, shortDate } from './account'
import type { Lifetime, Overview, Section, Tier } from './account'

/**
 * The identity plate, the loyalty ladder and the four lifetime figures.
 *
 * Compact for the reason the kit's own comment gives: everything above the tab
 * rail is paid for by every section switch, so it carries only what stays true
 * across all seven.
 *
 * Every figure here is measured, and every one of them can be absent. A customer
 * with nothing dispatched has no average lead time, and this renders «—» rather
 * than a zero that reads as "we shipped it instantly".
 */

/** Tier codes to the names the kit prints. Codes cross the wire (ADR-0012). */
const TIER_LABEL: Record<string, string> = {
  standard: 'Базовый',
  silver: 'Silver',
  gold: 'Gold',
}

function tierName(code: string): string {
  return TIER_LABEL[code] ?? code.toUpperCase()
}

/**
 * «Silver · −4%», or just the name at the bottom of the ladder.
 *
 * Exported because the window chrome shows the same string above every section
 * — the kit's `TIER :: SILVER · −4%` — and a second formatting of a discount is
 * a second chance to print a different one.
 */
export function tierBadge(tier: Tier): string {
  const off = Number(tier.discount_percent)
  return off > 0 ? `${tierName(tier.code)} · −${off}%` : tierName(tier.code)
}

export function AccountHeader({
  locale,
  overview,
  onGo,
}: {
  locale: Locale
  overview: Overview
  onGo: (section: Section) => void
}) {
  const { profile, tier, lifetime } = overview

  return (
    <section className="hv-cols hv-cols--2">
      <div className="hv-frame hv-frame--wide">
        <div className="hv-ident">
          <span className="hv-avatar">{monogram(profile.display_name, profile.email)}</span>
          <div className="hv-ident__body">
            <h1 className="hv-h hv-h--lead">{profile.display_name}</h1>
            <p className="hv-micro" style={{ marginTop: 'var(--hv-1)' }}>
              {[
                profile.email.toUpperCase(),
                profile.phone || null,
                `С ${shortDate(profile.created_at, locale)}`,
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>
            <div className="hv-row" style={{ marginTop: 'var(--hv-2)' }}>
              <span className="hv-state" data-state="idle">
                {tierBadge(tier)}
              </span>
              {/*
                The kit's two buttons, and they go where their labels say. The
                static kit opens a modal for the password; here the security
                section already holds the form, and sending somebody to the
                panel that owns a thing beats a second copy of it in a dialog.
              */}
              <button className="hv-btn hv-btn--sm" type="button" onClick={() => onGo('profile')}>
                Редактировать
              </button>
              <button className="hv-btn hv-btn--sm" type="button" onClick={() => onGo('sec')}>
                Сменить пароль
              </button>
            </div>
          </div>
        </div>

        <hr className="hv-hr" />

        <TierLadder locale={locale} tier={tier} saved={lifetime.saved} />
      </div>

      <Plates locale={locale} lifetime={lifetime} />
    </section>
  )
}

/**
 * The ladder, showing the gap rather than the badge already held.
 *
 * That is the block's whole point and the reason the backend computes the gap:
 * a client that subtracted for itself would have to know the rungs, and then
 * there would be two ladders.
 */
function TierLadder({
  locale,
  tier,
  saved,
}: {
  locale: Locale
  tier: Tier
  saved: string
}) {
  const fill = tier.progress_percent === null ? 100 : Number(tier.progress_percent)

  return (
    <div className="hv-tier">
      <div className="hv-row hv-row--between">
        <span className="hv-micro">
          {tier.next_code === null
            ? 'ВЫСШИЙ ТАРИФ ДОСТИГНУТ'
            : `ДО ТАРИФА ${tierName(tier.next_code).toUpperCase()} — ${roubles(tier.to_next, locale)}`}
        </span>
        <span className="hv-micro">СЭКОНОМЛЕНО {roubles(saved, locale)}</span>
      </div>
      <div className="hv-tier__track">
        <div
          className="hv-tier__fill"
          style={{ '--p': `${Number.isFinite(fill) ? fill : 0}%` } as React.CSSProperties}
        />
      </div>
      <div className="hv-tier__marks">
        {tier.steps.map((step) => (
          <b key={step.code} {...(step.reached ? { 'data-on': true } : {})}>
            {tierName(step.code).toUpperCase()} · {Number(step.discount_percent)}%
          </b>
        ))}
      </div>
    </div>
  )
}

/** The four plates. `hv-kpi__v` carries the figure, `__foot` the second fact. */
function Plates({ locale, lifetime }: { locale: Locale; lifetime: Lifetime }) {
  const spent = Number(lifetime.spend)
  const saved = Number(lifetime.saved)
  /*
    The kit writes «186 ТЫС ₽» and «14.8 ТЫС ₽» — a scale, not a rounding. Below
    a thousand the scale is the figure itself, so the unit disappears rather than
    printing «0.4 ТЫС».
  */
  const scaled = (value: number, digits: number) =>
    value >= 1000
      ? { figure: (value / 1000).toFixed(digits), unit: 'ТЫС ₽' }
      : { figure: new Intl.NumberFormat('ru-RU').format(Math.round(value)), unit: '₽' }

  const money = scaled(spent, 0)
  const savings = scaled(saved, 1)

  return (
    <div className="hv-grid hv-grid--2">
      <div className="hv-frame hv-kpi">
        <span className="hv-label">Заказов всего</span>
        <span className="hv-kpi__v">{lifetime.orders}</span>
        <span className="hv-kpi__foot">
          <span>В РАБОТЕ</span>
          <span className={lifetime.in_progress > 0 ? 'hv-live' : undefined}>
            {lifetime.in_progress}
          </span>
        </span>
      </div>

      <div className="hv-frame hv-kpi">
        <span className="hv-label">Потрачено</span>
        <span className="hv-kpi__v">
          {money.figure} <small>{money.unit}</small>
        </span>
        <span className="hv-kpi__foot">
          <span>СРЕДНИЙ ЧЕК</span>
          <span>{roubles(lifetime.average_order, locale)}</span>
        </span>
      </div>

      <div className="hv-frame hv-kpi" data-tone={saved > 0 ? 'good' : undefined}>
        <span className="hv-label">Сэкономлено</span>
        <span className={`hv-kpi__v${saved > 0 ? ' hv-good' : ''}`}>
          {savings.figure} <small>{savings.unit}</small>
        </span>
        <span className="hv-kpi__foot">
          <span>ОБЪЁМ + ТАРИФ</span>
          {/* The share of what was spent, not the tier's headline percentage —
              volume discounts land here too, and only the ratio covers both. */}
          <span>{spent > 0 ? `${Math.round((saved / spent) * 100)}%` : NONE}</span>
        </span>
      </div>

      <div className="hv-frame hv-kpi">
        <span className="hv-label">Средний срок</span>
        <span className="hv-kpi__v">
          {lifetime.average_days === null ? (
            NONE
          ) : (
            <>
              {lifetime.average_days} <small>СУТ</small>
            </>
          )}
        </span>
        <span className="hv-kpi__foot">
          <span>В СРОК</span>
          {/* Nothing dispatched yet means nothing to be punctual about. */}
          <span className={lifetime.on_time_of > 0 ? 'hv-good' : undefined}>
            {lifetime.on_time_of > 0 ? `${lifetime.on_time} из ${lifetime.on_time_of}` : NONE}
          </span>
        </span>
      </div>
    </div>
  )
}
