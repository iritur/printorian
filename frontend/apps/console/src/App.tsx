import { useState } from 'react'

import type { Locale } from '@printorian/ui'
import type { NavRoute, Realm } from '@printorian/ui'
import { AppShell, AuthPanel, SessionProvider, translate, useSession } from '@printorian/ui'

import { DashboardPage } from './dashboard/DashboardPage'
import { FleetPage } from './FleetPage'
import { JournalPage } from './JournalPage'
import { LibraryPage } from './LibraryPage'
import { MaterialsPage } from './MaterialsPage'
import { OrdersPage } from './OrdersPage'
import { PackagingPage } from './packaging/PackagingPage'
import { PostProductionPage } from './postproduction/PostProductionPage'
import { PrepPage } from './PrepPage'
import { UsersPage } from './UsersPage'

type Screen =
  | 'dashboard'
  | 'orders'
  | 'postproduction'
  | 'packaging'
  | 'prep'
  | 'library'
  | 'journal'
  | 'fleet'
  | 'materials'
  | 'users'

/**
 * Screens are offered by permission, not by role.
 *
 * Roles are a shorthand for a permission set and the set is what the API
 * enforces, so a nav driven by role names would drift from what the server
 * actually allows the moment a role's contents change.
 */
const VIEW_PRODUCTION = 'view_production'
const PREPARE_PLATE = 'prepare_plate'
const VIEW_ALL_ORDERS = 'view_all_orders'
const MANAGE_USERS = 'manage_users'
const MANAGE_LIBRARY = 'manage_library'
const MANAGE_JOURNAL = 'manage_journal'

/**
 * Which territory this bundle is.
 *
 * Exported so `main.tsx` can stamp it on `<html>` before the first render. The
 * shell stamps it too, but the sign-in door is drawn *outside* the shell — and a
 * door into the пульт that does not carry the hazard rail is the one screen
 * where the realm signal would be missing at exactly the moment someone is
 * deciding whether they are in the right place.
 */
export const REALM: Realm = 'control'

/**
 * The storefront, if this deployment publishes one reachable from the LAN.
 *
 * A URL rather than a screen key, for the same reason the storefront carries one
 * for the console: two bundles, two origins, so crossing is a page load. It
 * appears in the overlay's `ВИТРИНА` territory and never in the console's
 * masthead — an operator reaches the shop deliberately or not at all.
 */
const STOREFRONT_URL = import.meta.env.VITE_STOREFRONT_URL as string | undefined

const CROSS_REALM: NavRoute[] = STOREFRONT_URL
  ? [
      {
        key: 'storefront',
        label: 'Витрина',
        note: 'ГЛАВНАЯ · КОНФИГУРАТОР',
        realm: 'public',
        href: STOREFRONT_URL,
        mark: 'HOME',
        kicker: 'C:/PRINTORIAN',
        text: 'Что видит заказчик: расчёт цены, каталог, оформление заказа.',
        shape: 'cube',
      },
    ]
  : []

/**
 * The farm console.
 *
 * Served from the on-prem server on the LAN (ADR-0016). Everything here is staff
 * work — the fleet, the materials, the order desk, access — and none of it is
 * reachable from the storefront, which is a separate bundle on separate hardware.
 *
 * There is no anonymous view. The storefront exists for people who have not
 * signed in; a console with nobody behind it has nothing to show.
 */
function Shell() {
  // `null` until an actor exists, because which screen to open depends on what
  // they may reach. Defaulting to a fixed one lands an engineer on the order
  // desk — a screen absent from their own nav, whose API answers 403.
  const [screen, setScreen] = useState<Screen | null>(null)
  const [locale, setLocale] = useState<Locale>('ru')
  // `signOut` now lives in the shared shell; the door below only needs to know
  // whether anyone is behind it.
  const { actor, ready } = useSession()

  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)
  const may = (permission: string) => actor?.permissions.includes(permission) ?? false

  // `ready` distinguishes "not signed in" from "we have not asked yet". Without
  // it the console flashes a sign-in form at someone who is already signed in,
  // every time they open it.
  if (!ready)
    return (
      <div className="hv-door console console--door">
        <p className="hv-hint">{t('common.loading')}</p>
      </div>
    )

  if (!actor) {
    return (
      <div className="hv-door console console--door">
        <h1>Printorian</h1>
        {/*
          Staff accounts are created in the users screen by someone who already
          has one, so there is no self-registration here — a console that let
          anyone who reached it make themselves an account would be a door with
          no lock on a machine sitting on the shop floor.
        */}
        <AuthPanel locale={locale} hint={t('console.sign_in')} allowRegister={false} />
      </div>
    )
  }

  // One route table. The overlay filters it by permission and the tab row renders
  // the same list, so the two can never disagree about what this actor may reach.
  const routes: NavRoute[] = [
    {
      // First, and first for a reason: the summary is the screen someone opens
      // the console *to* look at, and the order desk is one click from it.
      key: 'dashboard',
      label: t('dashboard.title'),
      note: 'СОСТОЯНИЕ ФЕРМЫ · KPI',
      permission: VIEW_ALL_ORDERS,
      mark: 'FARM',
      kicker: 'C:/DASHBOARD/FARM.OVERVIEW',
      text: 'Заказы, парк, финансы и расписание на одном экране. Состояние каждой машины — светящимся квадратом, детали — по клику.',
      shape: 'grid',
    },
    {
      key: 'orders',
      label: t('orders.all.title'),
      note: 'ЗАКАЗЫ · СТАТУСЫ · ВОЗВРАТЫ',
      permission: VIEW_ALL_ORDERS,
      mark: 'DESK',
      kicker: 'C:/ORDERING/DESK',
      text: 'Все заказы фермы, перевод по этапам, возвраты и маржа по каждой позиции.',
      shape: 'grid',
    },
    {
      // Between the order desk and the prep queue, because that is where it sits
      // in the flow: the print is done, and the part is now a person's problem.
      key: 'postproduction',
      label: t('pp.title'),
      note: 'ЗАДАНИЯ · ИНСТРУКЦИИ',
      permission: VIEW_PRODUCTION,
      mark: 'POST',
      kicker: 'C:/PRODUCTION/POSTPROCESS',
      text: 'Очередь заданий поста, пошаговые инструкции с нормами времени и накопленные отметки операторов.',
      shape: 'pipe',
    },
    {
      // After the post and before prep in the list, because that is where it
      // sits in the flow: the part is finished, inspected, and now has to reach
      // a van before half past seven.
      key: 'packaging',
      label: t('pk.title'),
      note: 'ОЧЕРЕДЬ · ТАРА · ОТСЕЧКА',
      permission: VIEW_PRODUCTION,
      mark: 'PACK',
      kicker: 'C:/PRODUCTION/PACKING/QUEUE',
      text: 'Очередь упаковки по времени забора курьера, подбор тары по габариту и комплектность против заказа.',
      shape: 'pipe',
    },
    {
      key: 'prep',
      label: t('prep.title'),
      note: 'НАРЕЗКА · ПЛАСТИНЫ',
      permission: PREPARE_PLATE,
      mark: 'PREP',
      kicker: 'C:/PRODUCTION/PREP.QUEUE',
      text: 'Очередь на нарезку: модель на скачивание, готовая пластина обратно (ADR-0006).',
      shape: 'pipe',
    },
    {
      key: 'library',
      label: 'Каталог',
      note: 'МОДЕЛИ · ПУБЛИКАЦИЯ',
      permission: MANAGE_LIBRARY,
      mark: 'LIB',
      kicker: 'C:/CATALOG/CURATION',
      text: 'Библиотека моделей витрины: загрузка геометрии, описание, оценки и публикация.',
      shape: 'stack',
    },
    {
      key: 'journal',
      label: 'Журнал',
      note: 'ОТЧЁТЫ · ПУБЛИКАЦИЯ',
      permission: MANAGE_JOURNAL,
      mark: 'LOG',
      kicker: 'C:/JOURNAL/EDITOR',
      text: 'Публичные отчёты фермы: разделы, блоки статьи и публикация. Черновик виден только здесь.',
      shape: 'stack',
    },
    {
      key: 'fleet',
      label: t('nav.fleet'),
      note: 'ПАРК · СОСТОЯНИЕ · СЕРВИС',
      permission: VIEW_PRODUCTION,
      mark: 'FARM',
      kicker: 'C:/PRODUCTION/FLEET',
      text: 'Состояние каждой машины в реальном времени, карта обслуживания, слоты AMS и амортизация.',
      shape: 'nodes',
    },
    {
      key: 'materials',
      label: t('materials.title'),
      note: 'СКЛАД · ПАРТИИ · ЦЕНЫ',
      permission: VIEW_PRODUCTION,
      mark: 'STOCK',
      kicker: 'C:/INVENTORY/MATERIALS',
      text: 'Свойства, остатки, размещение по полкам и слотам, цена закупки и цена для заказчика.',
      shape: 'stack',
    },
    {
      key: 'users',
      label: t('users.title'),
      note: 'ДОСТУП · РОЛИ · СЕАНСЫ',
      permission: MANAGE_USERS,
      mark: 'AUTH',
      kicker: 'C:/IDENTITY/USERS',
      text: 'Учётные записи, роли как наборы прав, активные сессии. Экраны показываются по правам, а не по роли.',
      shape: 'key',
    },
    ...CROSS_REALM,
  ]
  // The masthead lists only what this actor may reach *and* only this realm's
  // own screens. The overlay is where the other territory becomes visible.
  const tabs = routes.filter((route) => !route.href && (!route.permission || may(route.permission)))

  // The screen actually shown: the chosen one while it is still reachable, else
  // the first one that is. Permissions can change under a signed-in session — a
  // role edited by an owner arrives on the next `/auth/me` — so this is checked
  // on every render rather than once at sign-in.
  const active = tabs.find((route) => route.key === screen)?.key ?? tabs[0]?.key

  if (!active) {
    // Signed in, and entitled to nothing here. Saying so beats an empty console
    // that looks broken.
    return (
      <div className="hv-door console console--door">
        <h1>Printorian</h1>
        <p className="hv-hint">{t('error.permission_denied')}</p>
      </div>
    )
  }

  const paths: Record<Screen, string> = {
    dashboard: '/DASHBOARD/FARM.OVERVIEW',
    orders: '/ORDERS/DESK',
    postproduction: '/PRODUCTION/POSTPROCESS',
    packaging: '/PRODUCTION/PACKING/QUEUE',
    prep: '/PRODUCTION/PREP.QUEUE',
    library: '/CATALOG/CURATION',
    journal: '/JOURNAL/EDITOR',
    fleet: '/FLEET/PRINTERS',
    materials: '/INVENTORY/MATERIALS',
    users: '/IDENTITY/USERS.DB',
  }

  return (
    <AppShell
      locale={locale}
      onLocaleChange={setLocale}
      realm={REALM}
      tab={`${tabs.find((route) => route.key === active)?.label ?? ''} :: ${t('console.title')}`}
      meta={[{ label: 'REALM', value: 'IDENTITY.LOCAL' }]}
      path={paths[active as Screen]}
      routes={routes}
      current={active}
      onNavigate={(key) => setScreen(key as Screen)}
      statusNote="PRINTORIAN · ПУЛЬТ ЦЕХА · ЛОКАЛЬНАЯ СЕТЬ"
    >
      {active === 'dashboard' && (
        <DashboardPage locale={locale} onOpenOrders={() => setScreen('orders')} />
      )}
      {active === 'orders' && <OrdersPage locale={locale} />}
      {active === 'postproduction' && <PostProductionPage locale={locale} />}
      {active === 'packaging' && <PackagingPage locale={locale} />}
      {active === 'prep' && <PrepPage locale={locale} />}
      {active === 'library' && <LibraryPage locale={locale} />}
      {active === 'journal' && <JournalPage locale={locale} />}
      {active === 'fleet' && <FleetPage locale={locale} />}
      {active === 'materials' && <MaterialsPage locale={locale} />}
      {active === 'users' && <UsersPage locale={locale} />}
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
