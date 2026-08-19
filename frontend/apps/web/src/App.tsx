import { useEffect, useState } from 'react'

import type { Locale, NavRoute, Realm } from '@printorian/ui'
import { AppShell, SessionProvider, translate } from '@printorian/ui'

import { AccountPage } from './AccountPage'
import { CabinetPage } from './CabinetPage'
import { CatalogPage } from './CatalogPage'
import type { CatalogPick } from './CatalogPage'
import { CheckoutPage } from './CheckoutPage'
import { ConfiguratorPage } from './ConfiguratorPage'
import type { CheckoutHandoff } from './ConfiguratorPage'
import { JournalPage } from './JournalPage'
import { JournalPostPage } from './JournalPostPage'
import { PromoPage } from './PromoPage'
import type { Section } from './account'

type Screen = 'promo' | 'catalog' | 'configure' | 'checkout' | 'cabinet' | 'account' | 'journal'

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
  account: '/account',
  journal: '/journal',
}

/** The account's seven sections, deep-linked as `/account/<section>`. */
const SECTIONS: readonly Section[] = [
  'profile',
  'orders',
  'models',
  'addr',
  'pay',
  'notify',
  'sec',
]

/** Where the address bar puts you. */
interface Place {
  screen: Screen
  report: string | null
  section: Section
  /** The order number the cabinet has open, or `null` for the newest. */
  tracked: string | null
}

/** The address bar, read into a place. */
function locate(pathname: string): Place {
  const path = pathname.replace(/\/+$/, '') || '/'

  const article = /^\/journal\/([^/]+)$/.exec(path)
  if (article) {
    return {
      screen: 'journal',
      report: decodeURIComponent(article[1] as string),
      section: 'profile',
      tracked: null,
    }
  }

  /*
    The account's sections are addressable. Seven panels behind one URL means
    «пришлите мне ссылку на адреса» cannot be answered, and it means a browser
    Back out of «Безопасность» leaves the whole screen rather than the section.
  */
  const inside = /^\/account\/([a-z]+)$/.exec(path)
  const named = SECTIONS.find((key) => key === inside?.[1])
  if (named) return { screen: 'account', report: null, section: named, tracked: null }

  const order = /^\/cabinet\/([A-Za-z0-9-]+)$/.exec(path)
  if (order) {
    return {
      screen: 'cabinet',
      report: null,
      section: 'profile',
      tracked: decodeURIComponent(order[1] as string).toUpperCase(),
    }
  }

  const found = (Object.entries(PATHS) as [Screen, string][]).find(
    ([, value]) => value === path,
  )
  return { screen: found?.[0] ?? 'promo', report: null, section: 'profile', tracked: null }
}

/** …and back out again. */
function address(place: Omit<Place, 'screen'> & { screen: Screen }): string {
  const { screen, report, section, tracked } = place
  if (screen === 'journal' && report) return `/journal/${encodeURIComponent(report)}`
  if (screen === 'account') return `/account/${section}`
  if (screen === 'cabinet' && tracked) return `/cabinet/${encodeURIComponent(tracked)}`
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
  /**
   * Which of the account's seven sections is open.
   *
   * Held here rather than inside `AccountPage` for the same reason `report` is:
   * it is part of the address, and the chrome's path strip has to be able to say
   * which section the customer is looking at.
   */
  const [section, setSection] = useState<Section>(opened.section)
  /**
   * Which order the tracking screen has open, by number, or `null` for the
   * newest. In the address for the same reason the journal's slug is: «где мой
   * заказ» is a question somebody forwards to support with a link.
   */
  const [tracked, setTracked] = useState<string | null>(opened.tracked)
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
    const next = address({ screen, report, section, tracked })
    if (window.location.pathname === next) return
    window.history.pushState(null, '', next)
  }, [screen, report, section, tracked])

  useEffect(() => {
    const back = () => {
      const there = locate(window.location.pathname)
      setScreen(there.screen)
      setReport(there.report)
      setSection(there.section)
      setTracked(there.tracked)
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
  /** Into the configurator, optionally on a model already chosen. */
  const onConfigure = (chosen: CatalogPick | null) => {
    setPick(chosen)
    setScreen('configure')
  }

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
    /*
      The account opens on «Профиль» when reached from the masthead. Not because
      the last section is uninteresting — because the masthead link means "take
      me to my account", and landing on «Безопасность» because that is where the
      customer was a week ago answers a question nobody asked.
    */
    if (next === 'account') setSection('profile')
    // The masthead's «Мои заказы» means "show me my orders", not "show me the
    // one I was reading last week". The screen opens on the newest.
    if (next === 'cabinet') setTracked(null)
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
      key: 'account',
      label: 'Кабинет',
      note: 'ПРОФИЛЬ · АДРЕСА · МОДЕЛИ',
      mark: 'ACCT',
      kicker: 'C:/CABINET/ACCOUNT',
      text: 'Профиль, тариф и лимиты, сохранённые адреса, загруженные модели, документы и сеансы. Всё, что ферма о вас хранит — и кнопка, чтобы это забрать.',
      shape: 'grid',
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
    The default path per screen. The strip's *facts* are reported by the screens
    themselves through `useChrome`, and so is any path tail that depends on what
    is loaded — see the configurator's `QUOTE.LIVE`.
  */
  const paths: Record<Screen, string> = {
    promo: '/STORE/HOME',
    catalog: '/CATALOG/LOCAL.LIBRARY',
    // The `/QUOTE.LIVE` tail is appended by the configurator itself, which is
    // the only thing that knows whether there is a live quote on screen.
    configure: '/STORE/CONFIGURATOR',
    checkout: '/STORE/CHECKOUT',
    cabinet: tracked ? `/CABINET/ORDERS/${tracked}` : '/CABINET/ORDERS',
    // The kit's own `C:/PRINTORIAN/CABINET/ACCOUNT/…` tail, with the section
    // rather than the account id: the id is the customer's and printing it in
    // the chrome would put it on every screenshot they ever send to support.
    account: `/CABINET/ACCOUNT/${section.toUpperCase()}`,
    // The kit's own two paths: the index and one report inside it.
    journal: report ? `/JOURNAL/REPORT.${report.toUpperCase()}` : '/JOURNAL/REPORTS',
  }

  return (
    <AppShell
      locale={locale}
      onLocaleChange={setLocale}
      realm={REALM}
      tab={`${routes.find((route) => route.key === screen)?.label ?? t('nav.configure')} :: Printorian`}
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
          onCatalog={() => go('catalog')}
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
        <CatalogPage locale={locale} onConfigure={(chosen) => onConfigure(chosen ?? null)} />
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

      {screen === 'cabinet' && (
        <CabinetPage
          locale={locale}
          open={tracked}
          onOpen={setTracked}
          onConfigure={(asset) =>
            onConfigure(
              asset
                ? {
                    slug: '',
                    href: `/api/account/models/${asset.id}/file`,
                    code: asset.name.replace(/\.[^.]+$/, ''),
                    title: asset.name,
                    material: null,
                  }
                : null,
            )
          }
        />
      )}

      {screen === 'account' && (
        <AccountPage
          locale={locale}
          section={section}
          onSection={setSection}
          onCabinet={() => go('cabinet')}
          onConfigure={(chosen) => onConfigure(chosen ?? null)}
        />
      )}
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
