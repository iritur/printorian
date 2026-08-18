import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import type { Locale } from '../i18n/messages'
import { translate } from '../i18n/translate'
import { NavOverlay } from '../nav/NavOverlay'
import type { NavRoute } from '../nav/NavOverlay'
import { useSession } from '../session/session'
import { RealmBadge } from './RealmBadge'
import { StatusBar } from './StatusBar'
import { ThemeSwitch } from './ThemeSwitch'
import { applyRealm } from './realm'
import type { Realm } from './realm'
import { useHealth } from './useHealth'

/** One `KEY :: value` pair in the chrome's meta strip. */
export interface MetaItem {
  label: string
  value: string
}

export interface AppShellProps {
  locale: Locale
  onLocaleChange: (locale: Locale) => void
  /**
   * Which territory this bundle is. Lands on `<html data-realm>`, where
   * `realm.css` draws the hazard rail, the ground and the nav density from it.
   *
   * A prop rather than something the shell infers: the storefront and the
   * console are separate bundles on separate hardware (ADR-0016), and each one
   * knows which it is at build time. Inferring it from the route list would
   * make a permission change able to flip the rail.
   */
  realm: Realm
  /** Left of the chrome row: which screen this is, in the kit's `A :: B` form. */
  tab: string
  /**
   * The identifiers that make a screen's state reproducible — a quote id, a rate
   * snapshot, the mesh being priced. The kit puts them here rather than in the
   * body because they are what a support conversation needs and a customer never
   * reads.
   */
  meta?: MetaItem[]
  /** The path strip, without the `C:/PRINTORIAN` root the shell adds. */
  path: string
  routes: NavRoute[]
  current: string
  onNavigate: (key: string) => void
  /** Left of the status bar's clock. The farm's own identification. */
  statusNote: string
  children: ReactNode
}

/**
 * The window chrome every screen sits in.
 *
 * One component rather than a header per app: the storefront and the console
 * draw the same chrome in the kit, and the two would drift the first time one
 * gained a field. The apps differ in what they *put* in it — a quote id here, a
 * printer count there — which is what `tab`, `meta` and `path` are for.
 *
 * The appbar is rendered from the same `routes` the overlay filters, so the row
 * and the menu can never disagree about what this actor may reach.
 */
export function AppShell({
  locale,
  onLocaleChange,
  realm,
  tab,
  meta = [],
  path,
  routes,
  current,
  onNavigate,
  statusNote,
  children,
}: AppShellProps) {
  const { actor, signOut } = useSession()
  const health = useHealth()
  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)

  // The realm the overlay should open filtered to, set by the badge and cleared
  // once the overlay has consumed it. `null` means "open on whatever you were
  // showing", which is what Ctrl+K should do.
  const [crossTo, setCrossTo] = useState<Realm | null>(null)
  const openMenu = useRef<(() => void) | null>(null)

  useEffect(() => {
    applyRealm(realm)
  }, [realm])

  /**
   * What the masthead lists.
   *
   * Two filters, and both matter. **Realm**: a customer's masthead lists only
   * customer destinations and a control masthead only farm ones — crossing is
   * deliberate, through the badge or the menu, not something you do by
   * misreading a link. **Permission**: the overlay dims what an actor cannot
   * reach, because you cannot ask for a role you cannot see, but a permanently
   * dimmed row in the app bar would just be clutter on every screen.
   */
  const reachable = routes.filter(
    (route) =>
      (route.realm ?? realm) === realm &&
      !route.href &&
      (!route.permission || (actor?.permissions.includes(route.permission) ?? false)),
  )

  return (
    <div className="hv-shell hv-graph">
      <header className="hv-chrome">
        <div className="hv-chrome__row">
          <span className="hv-tab">{tab}</span>

          <div className="hv-meta">
            {meta.map((item, index) => (
              <span key={item.label}>
                {index > 0 && <i className="hv-meta__sep" />}
                {item.label} :: <strong>{item.value}</strong>
              </span>
            ))}
          </div>

          <div className="hv-os">
            <span className="hv-os__label">PRINTORIAN OS ./v2.0</span>

            {/*
              The language switch belongs here rather than in the app bar, for
              the same reason the version string does: the choice is a property
              of the whole console, not of the section you happen to be in.
            */}
            <span className="hv-lang" role="group" aria-label="Language">
              {(['ru', 'en'] as const).map((code) => (
                <button
                  key={code}
                  type="button"
                  className="hv-lang__btn"
                  aria-pressed={locale === code}
                  onClick={() => onLocaleChange(code)}
                >
                  {code.toUpperCase()}
                </button>
              ))}
            </span>
          </div>
        </div>

        {/*
          The path strip is not decoration. It is the same vocabulary a log line
          and a support conversation use, so "where were you" has one answer.
        */}
        <div className="hv-path">
          <div className="hv-path__crumbs">
            C:/PRINTORIAN<span className="hv-path__here">{path}</span>
          </div>
          {/*
            Read from `/health/ready`, not asserted. A strip that says ONLINE
            because it is a literal is worse than no strip: it is a working
            indicator during exactly the outage it exists to report.
          */}
          <div className="hv-path__status">
            STATUS :: <b>{health.status}</b>
          </div>
        </div>
      </header>

      <nav className="hv-appbar">
        <span className="hv-appbar__brand">Printorian</span>

        <div className="hv-appbar__nav">
          {reachable.map((route) => (
            <button
              key={route.key}
              type="button"
              className="hv-appbar__link"
              // `aria-current` rather than a class: it is the same fact the
              // screen reader needs and the stylesheet already keys off it.
              aria-current={route.key === current ? 'page' : undefined}
              onClick={() => onNavigate(route.key)}
            >
              {route.label}
            </button>
          ))}
        </div>

        <div className="hv-appbar__right">
          <RealmBadge
            realm={realm}
            onCross={(to) => {
              setCrossTo(to)
              openMenu.current?.()
            }}
          />

          <NavOverlay
            locale={locale}
            realm={realm}
            routes={routes}
            current={current}
            onNavigate={onNavigate}
            filterTo={crossTo}
            onFilterConsumed={() => setCrossTo(null)}
            registerOpener={(open) => {
              openMenu.current = open
            }}
          />

          {actor && (
            <>
              <span className="hv-who">{actor.email}</span>
              <button type="button" className="hv-btn hv-btn--sm" onClick={() => void signOut()}>
                {t('checkout.sign_out')}
              </button>
            </>
          )}

          <ThemeSwitch locale={locale} />
        </div>
      </nav>

      <main className="hv-shell__body">{children}</main>

      <StatusBar note={statusNote} />
    </div>
  )
}
