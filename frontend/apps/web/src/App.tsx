import { useEffect, useState } from 'react'

import type { Locale, NavRoute, Realm } from '@printorian/ui'
import { AppShell, SessionProvider, translate } from '@printorian/ui'

import { CabinetPage } from './CabinetPage'
import { CatalogPage } from './CatalogPage'
import type { CatalogPick } from './CatalogPage'
import { CheckoutPage } from './CheckoutPage'
import { ConfiguratorPage } from './ConfiguratorPage'
import type { CheckoutHandoff, QuoteChrome } from './ConfiguratorPage'
import { JournalPage } from './JournalPage'
import { JournalPostPage } from './JournalPostPage'
import { PromoPage } from './PromoPage'

type Screen = 'promo' | 'catalog' | 'configure' | 'checkout' | 'cabinet' | 'journal'

/**
 * Where each screen lives in the address bar.
 *
 * Not a router library, and deliberately: this is a path-to-state map and a
 * `popstate` listener, which is the whole of what four screens and one article
 * route need. The comment on `Shell` used to say a router would earn its place
 * "once there are deep links to share" — the RSS feed is exactly that, because
 * every item in it points at `/journal/<slug>` and a feed whose links do not
 * resolve is not a feed.
 *
 * Checkout is absent on purpose. It only exists after a configurator handoff, so
 * a bookmark to it would open a page with no order behind it.
 */
const PATHS: Record<Exclude<Screen, 'checkout'>, string> = {
  promo: '/',
  catalog: '/catalog',
  configure: '/configurator',
  cabinet: '/cabinet',
  journal: '/journal',
}

/** The address bar, read into screen and open report. */
function locate(pathname: string): { screen: Screen; report: string | null } {
  const path = pathname.replace(/\/+$/, '') || '/'
  const article = /^\/journal\/([^/]+)$/.exec(path)
  if (article) return { screen: 'journal', report: decodeURIComponent(article[1] as string) }

  const found = (Object.entries(PATHS) as [Screen, string][]).find(
    ([, value]) => value === path,
  )
  return { screen: found?.[0] ?? 'promo', report: null }
}

/** …and back out again. */
function address(screen: Screen, report: string | null): string {
  if (screen === 'journal' && report) return `/journal/${encodeURIComponent(report)}`
  return PATHS[screen as Exclude<Screen, 'checkout'>] ?? '/'
}

/** Which territory this bundle is. Stamped on `<html>` by `main.tsx`. */
export const REALM: Realm = 'public'

/**
 * The console, if this deployment exposes one reachable from here.
 *
 * A URL rather than a screen key: the console is a separate bundle on separate
 * hardware (ADR-0016), so crossing is a page load. It appears in the overlay's
 * `ПУЛЬТ` territory and never in the storefront's masthead — the app bars do not
 * cross, which is what stops a customer reaching the farm by misreading a link.
 *
 * Absent when unset. A farm whose console is not published to the internet is
 * the normal case, and advertising a URL that does not resolve is worse than
 * listing nothing.
 */
const CONSOLE_URL = import.meta.env.VITE_CONSOLE_URL as string | undefined

const CROSS_REALM: NavRoute[] = CONSOLE_URL
  ? [
      {
        key: 'console',
        label: 'Пульт цеха',
        note: 'ПАРК · ЗАКАЗЫ · СКЛАД',
        realm: 'control',
        href: CONSOLE_URL,
        mark: 'FARM',
        kicker: 'C:/DASHBOARD/FARM.OVERVIEW',
        text: 'Производственный пульт фермы. Требуется учётная запись сотрудника.',
        shape: 'grid',
      },
    ]
  : []

/**
 * The storefront: configure → checkout → cabinet.
 *
 * Staff screens are not here and not hidden here. Since ADR-0016 the fleet,
 * materials, order desk and access screens are a separate app served from the
 * farm's own server, so this bundle does not contain them at all — a customer
 * cannot reach them by editing a permission in devtools, because the code is on
 * another machine.
 *
 * A router library would earn its place once there are deep links to share; three
 * screens and one handoff do not need one yet.
 */
function Shell() {
  /*
    The promo page is the door — but only for somebody arriving at the root. A
    reader following a link out of the RSS feed asked for a particular report, and
    opening the sales pitch instead would lose them the thing they clicked.
  */
  const opened = locate(window.location.pathname)
  const [screen, setScreen] = useState<Screen>(opened.screen)
  // The configurator's own type rather than a copy: a second declaration of the
  // same shape drifted the moment the handoff grew its resolved filament fields.
  const [handoff, setHandoff] = useState<CheckoutHandoff | null>(null)
  /**
   * The live quote's identifiers, reported up by the configurator.
   *
   * Here rather than in the page because the window chrome belongs to the shell:
   * the kit draws `MESH`, `RATES` and the `QUOTE.LIVE` path segment in the title
   * bar, above every screen.
   */
  /**
   * A catalogue model on its way into the configurator.
   *
   * Held here because it crosses screens. Cleared once the configurator has it,
   * so returning to the configurator later does not silently reload a model the
   * customer has since replaced with their own file.
   */
  const [pick, setPick] = useState<CatalogPick | null>(null)
  /**
   * Which report is open, or `null` for the index.
   *
   * A second piece of state rather than a sixth screen: the index and an
   * article are the same territory, and the chrome's path strip has to be able
   * to say which of the two the reader is looking at.
   */
  const [report, setReport] = useState<string | null>(opened.report)
  const [quoteChrome, setQuoteChrome] = useState<QuoteChrome | null>(null)
  const [locale, setLocale] = useState<Locale>('ru')

  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)

  /*
    Push the address after a navigation, and follow the browser's own buttons.

    `replaceState` when the address already matches, so the first render does not
    add a duplicate entry the reader has to press Back through twice. Checkout is
    skipped for the reason `PATHS` gives — there is no address that would restore
    it.
  */
  useEffect(() => {
    if (screen === 'checkout') return
    const next = address(screen, report)
    if (window.location.pathname === next) return
    window.history.pushState(null, '', next)
  }, [screen, report])

  useEffect(() => {
    const back = () => {
      const there = locate(window.location.pathname)
      setScreen(there.screen)
      setReport(there.report)
    }
    window.addEventListener('popstate', back)
    return () => window.removeEventListener('popstate', back)
  }, [])

  /**
   * Move to a screen, forgetting any catalogue model that was in flight.
   *
   * Every transition except the catalogue's own «Настроить и заказать» goes
   * through here. Without it a pick outlives the visit it was made in: a customer
   * who chose a catalogue model, then uploaded their own file, then stepped away
   * and came back would find the configurator quietly reload the catalogue one
   * over the top of their upload.
   */
  const go = (next: Screen) => {
    setPick(null)
    /*
      Always clears the open report, including when the destination *is* the
      journal — that click is the request for the index.

      This used to be guarded with `if (next !== 'journal')`, meaning to keep an
      article open across a round trip. It did the opposite of anything useful:
      pressing «Журнал» from inside a report left the report mounted and
      `setScreen('journal')` was a no-op because that was already the screen, so
      the masthead link did nothing at all.
    */
    setReport(null)
    setScreen(next)
  }

  /**
   * No permissions on any of these.
   *
   * The storefront's screens are for whoever walked in — the configurator prices
   * a model for someone who has never signed in, and the cabinet shows its own
   * sign-in form rather than disappearing from the nav. Gating them would hide
   * the shop from its customers.
   */
  const routes: NavRoute[] = [
    {
      key: 'promo',
      label: t('nav.about'),
      note: 'ГЛАВНАЯ · ВИТРИНА',
      mark: 'HOME',
      kicker: 'C:/PRINTORIAN',
      text: 'Что делает ферма и почему цена показывается построчно. Единственный экран, обращённый наружу.',
      shape: 'cube',
    },
    {
      key: 'catalog',
      label: 'Каталог',
      note: 'ГОТОВЫЕ МОДЕЛИ',
      mark: 'LIB',
      kicker: 'C:/CATALOG/LOCAL.LIBRARY',
      text: 'Локальная библиотека проверенных моделей. Время и цена — факт с последней печати, а не оценка по объёму.',
      shape: 'stack',
    },
    {
      key: 'configure',
      label: t('nav.configure'),
      note: 'РАСЧЁТ · ЗАГРУЗКА МОДЕЛИ',
      mark: 'QUOTE',
      kicker: 'C:/STORE/CONFIGURATOR',
      text: 'Загрузка модели, подбор материала, цвета и количество — с прозрачной ценой, которая пересчитывается на каждом изменении.',
      shape: 'cube',
    },
    {
      key: 'cabinet',
      label: t('nav.my_orders'),
      note: 'СТАТУС · ОЧЕРЕДЬ · ИСТОРИЯ',
      mark: 'TRACK',
      kicker: 'C:/CABINET/ORDERS',
      text: 'Девять этапов от оплаты до отправки. Если производство выходит за обещанный срок, цена снижается автоматически.',
      shape: 'pipe',
    },
    {
      key: 'journal',
      label: 'Журнал',
      note: 'ОТЧЁТЫ · РАСЧЁТЫ · ОШИБКИ',
      mark: 'LOG',
      kicker: 'C:/JOURNAL/REPORTS',
      text: 'Как ферма устроена изнутри: полные расчёты себестоимости, разборы материалов и решения, которые пришлось откатывать.',
      shape: 'stack',
    },
    ...CROSS_REALM,
  ]

  /*
    The chrome's meta strip, per screen.

    The configurator's three come from the live quote, which is what the kit shows
    there: the mesh being priced, its content address, and the rate snapshot the
    figures came from. Short forms, because the strip is one line — the full digest
    and snapshot id are on the elements that own them.
  */
  const meta =
    screen === 'configure' && quoteChrome
      ? [
          { label: 'MESH', value: quoteChrome.fileName.toUpperCase() },
          { label: 'SHA', value: quoteChrome.sha256.slice(0, 12).toUpperCase() },
          {
            label: 'RATES',
            value: `SNAP.${quoteChrome.rateSnapshotId.replace(/^rates_/, '').slice(0, 6).toUpperCase()}`,
          },
        ]
      : screen === 'checkout' && handoff
        ? [
            { label: 'MESH', value: handoff.model.fileName.toUpperCase() },
            { label: 'MATERIAL', value: handoff.materialCode.toUpperCase() },
            /*
              The kit's third item is `LOCK :: 23 Ч 41 М`, a countdown on a quote
              that expires. Nothing here expires — a breakdown is pinned to its
              order and never recomputed (ADR-0002) — so a timer would be counting
              down to an event that does not happen. The snapshot id is the fact
              underneath the kit's intent: it is what makes this exact price
              reproducible later.
            */
            ...(handoff.breakdown.rate_snapshot_id
              ? [
                  {
                    label: 'RATES',
                    value: `SNAP.${handoff.breakdown.rate_snapshot_id
                      .replace(/^rates_/, '')
                      .slice(0, 6)
                      .toUpperCase()}`,
                  },
                ]
              : []),
          ]
        : []

  const paths: Record<Screen, string> = {
    promo: '/STORE/HOME',
    catalog: '/CATALOG/LOCAL.LIBRARY',
    // The kit's `QUOTE.LIVE` tail, and it means what it says: there is a live
    // quote on screen. Before an upload there is not, so the segment is absent.
    configure: quoteChrome ? '/STORE/CONFIGURATOR/QUOTE.LIVE' : '/STORE/CONFIGURATOR',
    checkout: '/STORE/CHECKOUT',
    cabinet: '/CABINET/ORDERS',
    // The kit's own two paths: the index and one report inside it.
    journal: report ? `/JOURNAL/REPORT.${report.toUpperCase()}` : '/JOURNAL/REPORTS',
  }

  return (
    <AppShell
      locale={locale}
      onLocaleChange={setLocale}
      realm={REALM}
      tab={`${routes.find((route) => route.key === screen)?.label ?? t('nav.configure')} :: Printorian`}
      meta={meta}
      path={paths[screen]}
      routes={routes}
      current={screen === 'checkout' ? 'configure' : screen}
      onNavigate={(key) => go(key as Screen)}
      statusNote="PRINTORIAN · АВТОМАТИЧЕСКАЯ ФЕРМА 3D-ПЕЧАТИ"
    >
      {screen === 'promo' && (
        <PromoPage
          locale={locale}
          onStart={() => go('configure')}
          onCabinet={() => go('cabinet')}
        />
      )}

      {screen === 'journal' &&
        (report ? (
          <JournalPostPage
            locale={locale}
            slug={report}
            onBack={() => setReport(null)}
            onRead={setReport}
            onConfigure={() => go('configure')}
          />
        ) : (
          <JournalPage locale={locale} onRead={setReport} />
        ))}

      {screen === 'catalog' && (
        <CatalogPage
          locale={locale}
          onConfigure={(chosen) => {
            setPick(chosen ?? null)
            setScreen('configure')
          }}
        />
      )}

      {screen === 'configure' && (
        <ConfiguratorPage
          locale={locale}
          onCheckout={(next) => {
            setHandoff(next)
            go('checkout')
          }}
          // The kit's «Выбрать из каталога», which is the other way into a quote:
          // the catalogue's own «Настроить и заказать» comes back the other way.
          onCatalog={() => go('catalog')}
          onQuoteChrome={setQuoteChrome}
          fromCatalog={pick}
        />
      )}

      {screen === 'checkout' && handoff && (
        <CheckoutPage
          locale={locale}
          config={handoff.config}
          model={handoff.model}
          breakdown={handoff.breakdown}
          materialCode={handoff.materialCode}
          colors={handoff.colors}
          onBack={() => go('configure')}
          onDone={() => go('cabinet')}
        />
      )}

      {screen === 'cabinet' && <CabinetPage locale={locale} />}
    </AppShell>
  )
}

export function App() {
  return (
    <SessionProvider>
      <Shell />
    </SessionProvider>
  )
}
