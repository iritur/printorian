export { DataTable } from './DataTable/DataTable'
export { StatusTags } from './DataTable/StatusTags'
export { compareValues, nextSort, sortRows } from './DataTable/sorting'
export type {
  Column,
  DataTableProps,
  SortDirection,
  SortState,
  Sortable,
  StatusTag,
  Tone,
} from './DataTable/types'

export { DEFAULT_LOCALE, catalogues, en, ru } from './i18n/messages'
export type { Locale, MessageKey, Messages } from './i18n/messages'
export { plural } from './i18n/plural'
export { createTranslator, translate, translateError } from './i18n/translate'
export type { ApiErrorBody, Details, Translator } from './i18n/translate'

export { amsSlotLabel, formatLocation, summarizeLocations } from './inventory/location'
export type { LotLocation } from './inventory/location'

export { DeltaPreview } from './pricing/DeltaPreview'
export { PriceBreakdown } from './pricing/PriceBreakdown'
export { formatBasis, formatChange, formatMoney, lineLabel, snapshotLabel } from './pricing/format'
export type {
  Basis,
  BasisKind,
  Breakdown,
  BreakdownLine,
  Delta,
  DeltaLine,
} from './pricing/format'

// The session is shared infrastructure, not a storefront detail: both the public
// app and the farm console sign the same people in against the same cookie.
export { AuthPanel } from './session/AuthPanel'
export { SessionProvider, api, useSession } from './session/session'
export type { Actor } from './session/session'

export { OrdersScreen } from './orders/OrdersScreen'
export type { Order, OrderEvent, OrdersScreenProps } from './orders/OrdersScreen'

// The navigation overlay, fed by `actor.permissions` — one component rather than
// a route list copied into every screen. It is also the one place both realms
// are visible at once, and so the one place the boundary is drawn.
export { NavOverlay } from './nav/NavOverlay'
export type { NavOverlayProps, NavRoute } from './nav/NavOverlay'
export { SHAPES } from './nav/shapes'
export type { ShapeName } from './nav/shapes'

// The window chrome both apps sit in, and the kit's Void/Paper switch.
export { AppShell } from './shell/AppShell'
// The last thing between a render error and a blank page. Both apps wrap their
// root in it; there was no boundary anywhere before.
export { ErrorBoundary } from './shell/ErrorBoundary'
export { useChrome } from './shell/chrome'
export type { Chrome, MetaItem } from './shell/chrome'
export { TabRail, TabView } from './shell/TabRail'
export type { TabRailProps } from './shell/TabRail'
export type { AppShellProps } from './shell/AppShell'
export type { ErrorBoundaryProps } from './shell/ErrorBoundary'

// The kit's popup, with the behaviour attached once: Esc, backdrop, focus in
// and back, a tab trap, and no scrolling behind it.
export { Modal } from './shell/Modal'
export type { ModalProps } from './shell/Modal'
export { THEMES, ThemeSwitch } from './shell/ThemeSwitch'
export type { Theme } from './shell/ThemeSwitch'
export { StatusBar } from './shell/StatusBar'

// The public/control split: витрина and пульт.
export { OTHER_REALM, REALM_LABEL, applyRealm } from './shell/realm'
export type { Realm } from './shell/realm'
export { useHealth } from './shell/useHealth'
export type { Health, HealthStatus } from './shell/useHealth'

// The journal — the farm's public reports, and the renderer both apps share.
export { Article } from './journal/Article'
export type { ArticleProps } from './journal/Article'
export { BLOCK_KINDS, SECTIONS, SECTION_META, anchorOf, slugify } from './journal/blocks'
export type {
  Block,
  Callout,
  Code,
  FigureRow,
  Figures,
  FigureTone,
  Heading,
  ListBlock,
  Paragraph,
  Quote,
  Section,
  TableBlock,
} from './journal/blocks'
