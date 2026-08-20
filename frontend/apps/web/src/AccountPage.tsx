import { useEffect, useState } from 'react'

import type { Locale } from '@printorian/ui'
import { AuthPanel, TabRail, TabView, api, useChrome, useSession } from '@printorian/ui'

import { AccountAddresses } from './AccountAddresses'
import { AccountBilling } from './AccountBilling'
import { AccountHeader, tierBadge } from './AccountHeader'
import { AccountModels } from './AccountModels'
import { AccountNotify } from './AccountNotify'
import { AccountOrders } from './AccountOrders'
import { AccountProfile } from './AccountProfile'
import { AccountSecurity } from './AccountSecurity'
import { shortDate } from './account'
import type { ModelAsset, Overview, Profile, Section } from './account'
import type { CatalogPick } from './CatalogPage'

/**
 * «Кабинет» — `design/account.html`, all seven sections.
 *
 * Distinct from «Мои заказы», which is `cabinet.html` and tracks one order
 * through nine stages. This is the *record*: who the customer is, what they
 * have spent, where things go, what they have uploaded and how to get out. The
 * kit keeps them as two screens and so does this, because the questions are
 * different — «где мой заказ?» has a different urgency from «смените мне
 * пароль», and answering both on one screen means answering neither first.
 *
 * The header is loaded once and shared by every section. It is the only thing
 * paid for on each switch, which is why the kit keeps it to the identity plate,
 * the ladder and four figures.
 */

const SECTIONS: { key: Section; label: string }[] = [
  { key: 'profile', label: 'Профиль' },
  { key: 'orders', label: 'Заказы' },
  { key: 'models', label: 'Мои модели' },
  { key: 'addr', label: 'Адреса доставки' },
  { key: 'pay', label: 'Оплата и документы' },
  { key: 'notify', label: 'Уведомления' },
  { key: 'sec', label: 'Безопасность' },
]

export function AccountPage({
  locale,
  section,
  onSection,
  onCabinet,
  onConfigure,
}: {
  locale: Locale
  section: Section
  onSection: (section: Section) => void
  /** «Ход выполнения» — the tracking screen, which owns the pipeline. */
  onCabinet: () => void
  /** Opens the configurator, optionally on one of the customer's own uploads. */
  onConfigure: (pick?: CatalogPick) => void
}) {
  const { actor, ready } = useSession()
  const [overview, setOverview] = useState<Overview | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!actor) {
      setOverview(null)
      return
    }
    let live = true
    setFailed(false)
    void api
      .get<Overview>('/account')
      .then((body) => live && setOverview(body))
      .catch(() => live && setFailed(true))
    return () => {
      live = false
    }
  }, [actor])

  /*
    `null` until the fetch lands, rather than a placeholder: the strip states a
    tier and a joining date, and the title bar is the one place on screen that is
    supposed to be fact.

    The kit's third item is `ACCOUNT :: 2AFPQJJTVNGDOBFF`, and it is left out —
    that is the customer's own identifier, and the chrome is the strip that ends
    up in every screenshot somebody sends to support.
  */
  useChrome(
    overview
      ? {
          meta: [
            { label: 'TIER', value: tierBadge(overview.tier).toUpperCase() },
            // The kit writes «С 12.03.2026» with no separator; the shell puts
            // `::` between every label and value, and «С :: 18.08.2026» reads as
            // a preposition dangling off a colon. Same fact, named.
            { label: 'РЕГИСТРАЦИЯ', value: shortDate(overview.profile.created_at, locale) },
          ],
        }
      : null,
  )

  if (!ready) return <p className="hv-hint">Загрузка…</p>

  /*
    The sign-in form rather than a redirect or a disappearing nav entry. This is
    the same choice the cabinet makes: a customer who bookmarked their account
    and came back a week later should land on a way in, not on the promo page
    wondering whether the link still works.
  */
  if (!actor) {
    return (
      <div className="hv-cols hv-cols--2">
        <section className="hv-frame hv-frame--wide">
          <span className="hv-label">Кабинет</span>
          <h1 className="hv-h hv-h--lead" style={{ marginTop: 'var(--hv-1)' }}>
            Войдите, чтобы открыть свой кабинет
          </h1>
          <p className="hv-prose" style={{ fontSize: 'var(--hv-size-small)' }}>
            Профиль, история заказов, загруженные модели, адреса и документы — всё
            хранится на сервере фермы.
          </p>
        </section>
        <AuthPanel locale={locale} />
      </div>
    )
  }

  if (failed) return <p className="hv-hint hv-bad">Не удалось загрузить кабинет.</p>
  if (!overview) return <p className="hv-hint">Загрузка…</p>

  /** A saved profile replaces the header's copy without a second round trip. */
  const adopt = (profile: Profile) => setOverview({ ...overview, profile })

  /** One of the customer's own uploads, on its way into the configurator. */
  const reorder = (asset: ModelAsset) =>
    onConfigure({
      slug: '',
      // The asset route, not the catalogue's: `/catalog/{slug}/model` publishes
      // geometry the farm has chosen to publish and deliberately refuses to
      // serve somebody's own upload.
      href: `/api/account/models/${asset.id}/file`,
      code: asset.original_filename.replace(/\.[^.]+$/, ''),
      title: asset.original_filename,
      // No recommendation to carry: a customer's own upload has never been
      // through curation, so the configurator opens on its own default.
      material: null,
    })

  return (
    <div className="hv-stack hv-stack--4">
      <AccountHeader locale={locale} overview={overview} onGo={onSection} />

      <div className="hv-cols hv-cols--2l">
        <aside className="hv-sticky hv-stack">
          <section className="hv-panel">
            <div className="hv-panel__head">
              <span>Разделы</span>
              <span className="hv-panel__aside">{SECTIONS.length}</span>
            </div>
            <TabRail
              tabs={SECTIONS}
              current={section}
              onSelect={onSection}
              label="Разделы кабинета"
            />
            <div className="hv-panel__foot">
              <span>ДАННЫЕ ХРАНЯТСЯ НА СЕРВЕРЕ ФЕРМЫ</span>
            </div>
          </section>

          {/*
            The kit's «Сейчас печатается» panel. It shows one job with a live
            percentage, and that figure comes from the fleet — which the
            storefront cannot reach: the printers are on the farm's LAN and the
            telemetry socket is staff-only (ADR-0016). What *is* known here is
            how many of this customer's orders are still owed work, so the panel
            says that and sends them to the screen that tracks them.

            A progress bar filled from something else would have been the easy
            version and the dishonest one.
          */}
          <section className="hv-frame">
            <span className="hv-h">В работе</span>
            <div style={{ marginTop: 'var(--hv-2)' }}>
              <div className="hv-kpi__v">{overview.lifetime.in_progress}</div>
              <div className="hv-micro" style={{ marginTop: 'var(--hv-2)' }}>
                {overview.lifetime.in_progress > 0
                  ? 'ЭТАПЫ И ОЧЕРЕДЬ — В РАЗДЕЛЕ «МОИ ЗАКАЗЫ»'
                  : 'НЕЗАВЕРШЁННЫХ ЗАКАЗОВ НЕТ'}
              </div>
            </div>
            <button
              className="hv-btn hv-btn--block"
              type="button"
              style={{ marginTop: 'var(--hv-3)' }}
              onClick={onCabinet}
            >
              {overview.lifetime.in_progress > 0 ? 'Открыть заказы' : 'Собрать заказ'}
            </button>
          </section>
        </aside>

        <TabView name={section}>
          {section === 'profile' && (
            <AccountProfile
              locale={locale}
              profile={overview.profile}
              lifetime={overview.lifetime}
              onSaved={adopt}
            />
          )}
          {section === 'orders' && <AccountOrders locale={locale} onTrack={onCabinet} />}
          {section === 'models' && (
            <AccountModels
              locale={locale}
              onOrder={reorder}
              onUpload={() => onConfigure()}
            />
          )}
          {section === 'addr' && <AccountAddresses locale={locale} />}
          {section === 'pay' && <AccountBilling locale={locale} />}
          {section === 'notify' && <AccountNotify email={overview.profile.email} />}
          {section === 'sec' && <AccountSecurity locale={locale} />}
        </TabView>
      </div>
    </div>
  )
}

export { SECTIONS as ACCOUNT_SECTIONS }
