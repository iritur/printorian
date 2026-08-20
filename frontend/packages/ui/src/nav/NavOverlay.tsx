import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { Locale } from '../i18n/messages'
import { translate } from '../i18n/translate'
import { useSession } from '../session/session'
import { REALM_LABEL } from '../shell/realm'
import type { Realm } from '../shell/realm'
import { DecodedLabel } from './DecodedLabel'
import { SHAPES } from './shapes'
import type { ShapeName } from './shapes'

/**
 * One destination in the overlay.
 *
 * `permission` is what decides whether it is *reachable*. Roles are a shorthand
 * for a permission set and the set is what the API enforces, so a menu driven by
 * role names drifts from what the server allows the moment a role's contents
 * change. `undefined` means everyone sees it.
 *
 * A route the actor may not reach is **dimmed, not hidden**: you cannot ask for
 * a role you cannot see. That is why permission no longer removes the row.
 */
export interface NavRoute {
  key: string
  label: string
  /** The right-hand column: what the screen is for, in the kit's tracked caps. */
  note: string
  permission?: string
  /**
   * Which territory this destination belongs to. Defaults to the shell's own
   * realm, so a route table that predates the split still renders correctly.
   */
  realm?: Realm
  /**
   * An absolute URL, for a destination in the *other* app.
   *
   * The storefront and the console are separate bundles on separate hardware
   * (ADR-0016), so crossing territories is a page load, not a state change —
   * `onNavigate` has no key for a screen that is not in this bundle. Apps add
   * these only when the deployment actually exposes the other side; advertising
   * a URL that does not resolve is worse than not listing it.
   */
  href?: string
  /** The preview pane's four-letter stamp — `FARM`, `DESK`, `AUTH`. */
  mark?: string
  /** The path strip this destination shows, e.g. `C:/PRODUCTION/FLEET`. */
  kicker?: string
  /** One sentence on what the screen is for. */
  text?: string
  /** Up to three `[label, value]` figures. Placeholders until the API feeds them. */
  stats?: [string, string][]
  shape?: ShapeName
}

export interface NavOverlayProps {
  locale: Locale
  /** The realm the surrounding app is, used as the default for its routes. */
  realm: Realm
  routes: NavRoute[]
  /** The route the app is currently showing, so the overlay opens oriented. */
  current: string
  onNavigate: (key: string) => void
  /**
   * Open already filtered to one territory. Set by the realm badge, because the
   * only reason anyone clicks that badge is to cross.
   */
  filterTo?: Realm | null
  onFilterConsumed?: () => void
  /** Hand the shell a way to open this overlay (the badge needs one). */
  registerOpener?: (open: () => void) => void
}

type RealmFilter = 'all' | Realm

/** The order territories are drawn in: витрина above, пульт below. */
const TERRITORIES: Realm[] = ['public', 'control']

/**
 * The full-screen navigation console.
 *
 * A component fed by `actor.permissions`, which is what `design/js/menu.js` was
 * shaped for — it keeps its route table at the top precisely so this could be
 * one source rather than markup copied into every screen.
 *
 * It is a full-screen console rather than a dropdown on purpose: the same window
 * chrome as a real screen, so it reads as switching subsystems rather than
 * opening a drawer.
 *
 * This is also the one place both realms are visible at once, and therefore the
 * one place the boundary between them is drawn explicitly.
 */
export function NavOverlay({
  locale,
  realm,
  routes,
  current,
  onNavigate,
  filterTo = null,
  onFilterConsumed,
  registerOpener,
}: NavOverlayProps) {
  const { actor } = useSession()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [realmFilter, setRealmFilter] = useState<RealmFilter>('all')
  const input = useRef<HTMLInputElement | null>(null)
  // Where focus came from, so Esc puts it back rather than dropping it on body.
  const opener = useRef<HTMLElement | null>(null)

  const may = useCallback(
    (route: NavRoute) =>
      !route.permission || (actor?.permissions.includes(route.permission) ?? false),
    [actor],
  )

  /** Every route, tagged with its territory and whether it is locked. */
  const all = useMemo(
    () =>
      routes.map((route) => ({
        ...route,
        realm: route.realm ?? realm,
        locked: !may(route),
      })),
    [routes, realm, may],
  )

  const counts = useMemo(
    () => ({
      all: all.length,
      public: all.filter((route) => route.realm === 'public').length,
      control: all.filter((route) => route.realm === 'control').length,
    }),
    [all],
  )

  /** Search and realm filter, in one pass. */
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase(locale)
    return all.filter((route) => {
      if (realmFilter !== 'all' && route.realm !== realmFilter) return false
      if (!needle) return true
      return (
        route.label.toLocaleLowerCase(locale).includes(needle) ||
        route.note.toLocaleLowerCase(locale).includes(needle)
      )
    })
  }, [all, query, locale, realmFilter])

  /**
   * Rows grouped by territory, numbered **within** each.
   *
   * The numbering restarts on purpose: "витрина 03" and "пульт 03" are different
   * places, and one list running 1–20 would imply they are ranked against each
   * other. `index` is the position in the flat `visible` list, which is what the
   * keyboard cursor moves through.
   */
  const groups = useMemo(() => {
    let index = 0
    return TERRITORIES.map((territory) => {
      const rows = visible
        .filter((route) => route.realm === territory)
        .map((route, ordinal) => ({ route, ordinal: ordinal + 1, index: index++ }))
      return { territory, rows }
    }).filter((group) => group.rows.length > 0)
  }, [visible])

  const show = useCallback(() => {
    opener.current = document.activeElement as HTMLElement | null
    setQuery('')
    // Open sitting on the current entry. Falling back to 0 matters for a screen
    // the actor cannot reach from here — the list must never open on nothing.
    setActive(Math.max(0, all.findIndex((route) => route.key === current)))
    setOpen(true)
  }, [all, current])

  const hide = useCallback(() => {
    setOpen(false)
    setRealmFilter('all')
    opener.current?.focus()
  }, [])

  // The realm badge opens this already filtered to the other side.
  useEffect(() => {
    if (!filterTo) return
    setRealmFilter(filterTo)
    onFilterConsumed?.()
  }, [filterTo, onFilterConsumed])

  useEffect(() => {
    registerOpener?.(show)
  }, [registerOpener, show])

  // Ctrl/Cmd+K and `/` from anywhere. `/` is ignored while typing, or the
  // shortcut would eat the character in every search box in the app.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing =
        event.target instanceof HTMLElement &&
        (event.target.isContentEditable ||
          ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName))

      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        // Toggles: the same chord that opened it closes it, which is what every
        // command palette does and therefore what the hands expect.
        if (open) hide()
        else show()
        return
      }
      if (event.key === '/' && !open && !typing) {
        event.preventDefault()
        show()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, show, hide])

  useEffect(() => {
    if (open) input.current?.focus()
  }, [open])

  // Filtering can leave the cursor past the end of a shorter list.
  useEffect(() => {
    setActive((current) => Math.min(current, Math.max(0, visible.length - 1)))
  }, [visible.length])

  const go = (route: (typeof all)[number]) => {
    // A locked row is shown so the actor can see what they would need. Acting on
    // it would send them to a screen whose API answers 403, so it does nothing.
    if (route.locked) return
    // Crossing territories is a page load into the other bundle; staying inside
    // one is a state change. The route says which it is.
    if (route.href) {
      window.location.assign(route.href)
      return
    }
    onNavigate(route.key)
    hide()
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((current) => (current + 1) % Math.max(1, visible.length))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((current) => (current - 1 + visible.length) % Math.max(1, visible.length))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const chosen = visible[active]
      if (chosen) go(chosen)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      hide()
    }
  }

  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)
  const preview = visible[active]

  /**
   * Drives `.hv-menu__preview[data-swapping='true'] .hv-menu__pv`, the 180ms
   * two-step flicker the pane makes as its contents change.
   *
   * It has to be a timed flag rather than a CSS transition because the animation
   * is on *replaced content*: the flicker marks the swap itself, and there is no
   * property interpolating between the old route and the new one.
   */
  const [swapping, setSwapping] = useState(false)
  const previewKey = preview?.key

  useEffect(() => {
    if (!previewKey) return
    setSwapping(true)
    const timer = setTimeout(() => setSwapping(false), 200)
    return () => clearTimeout(timer)
  }, [previewKey])

  return (
    <>
      {/*
        The kit's own trigger, not a plain button wearing `hv-btn`.

        Three parts and each earns its place: the bars say "this opens
        something" before the label is read, the label says what, and the hint
        names the shortcut — which is real (Ctrl/Cmd+K, below) rather than
        decorative. `menu.css` already carries the styling, including the hover
        animation on the bars and the rule that drops the hint on a narrow
        screen where it would push the brand off the bar.
      */}
      <button
        type="button"
        className="hv-menu-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={show}
      >
        <span className="hv-menu-trigger__bars">
          <i />
          <i />
          <i />
        </span>
        <span>{t('nav.menu')}</span>
        <span className="hv-menu-trigger__hint">CTRL K</span>
      </button>

      {/*
        Rendered only while open rather than hidden. The overlay is a full-screen
        console with its own chrome; leaving it mounted would put a second copy
        of every route label into the accessibility tree of every screen.
      */}
      {open && (
        <div className="hv-menu" role="dialog" aria-modal="true" aria-label={t('nav.menu')}>
          <div className="hv-menu__bg" />
          <div className="hv-menu__frame" />
          <div className="hv-menu__scan" />

          <header className="hv-chrome">
            <div className="hv-chrome__row">
              <span className="hv-tab">{t('nav.menu')}</span>
              <label className="hv-meta">
                <span>{t('nav.go_to')}</span>
                <input
                  ref={input}
                  className="hv-menu__query"
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  value={query}
                  placeholder={t('nav.filter')}
                  aria-label={t('nav.filter')}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={onKeyDown}
                />
                <i className="hv-menu__caret" />
                <i className="hv-meta__sep" />
                <span className="hv-menu__count">{visible.length}</span>
              </label>
              <div className="hv-os">
                <button
                  className="hv-os__x"
                  type="button"
                  onClick={hide}
                  aria-label={t('common.close')}
                >
                  ✕
                </button>
              </div>
            </div>
          </header>

          <div className="hv-menu__field">
            <nav className="hv-menu__list" onKeyDown={onKeyDown}>
              {/* Three filters, each carrying its own count. */}
              <div className="hv-menu__realms" role="group" aria-label={t('nav.realm_filter')}>
                {(['all', 'public', 'control'] as const).map((option) => (
                  <button
                    key={option}
                    type="button"
                    className="hv-menu__realm"
                    aria-pressed={realmFilter === option}
                    onClick={() => setRealmFilter(option)}
                  >
                    {option === 'all' ? t('nav.realm_all') : REALM_LABEL[option]}
                    <b>{counts[option]}</b>
                  </button>
                ))}
              </div>

              {groups.map((group, groupIndex) => (
                <div key={group.territory}>
                  {/*
                    The access border, drawn only between the two territories —
                    filtering to one side removes the boundary along with the
                    empty half, because there is no longer anything to cross.
                  */}
                  {groupIndex > 0 && (
                    <div className="hv-menu__border">
                      <span>{t('nav.border')}</span>
                    </div>
                  )}

                  <div className="hv-menu__terr">
                    <b>{REALM_LABEL[group.territory]}</b>
                    <span>{group.rows.length}</span>
                  </div>

                  {group.rows.map(({ route, ordinal, index }) => {
                    const note = route.locked
                      ? t('nav.locked')
                      : route.key === current
                        ? t('nav.you_are_here')
                        : route.note
                    return (
                      <button
                        key={route.key}
                        type="button"
                        className="hv-menu__item"
                        // Feeds `animation-delay: calc(var(--i) * 34ms + 140ms)`
                        // — the entry stagger, which is keyframes, not script.
                        style={{ ['--i' as string]: index }}
                        data-active={index === active}
                        data-realm={route.realm}
                        data-locked={route.locked}
                        aria-disabled={route.locked}
                        // Stable through the decode animation below, which
                        // replaces the visible characters frame by frame.
                        aria-label={`${route.label} — ${note}`}
                        // Hovering moves the cursor, so pointer and keyboard
                        // agree on what "selected" means rather than keeping two
                        // ideas of it.
                        onMouseEnter={() => setActive(index)}
                        onClick={() => go(route)}
                      >
                        <span className="hv-menu__n">{String(ordinal).padStart(2, '0')}</span>
                        <span className="hv-menu__label" data-text={route.label}>
                          <i className="hv-menu__flag" aria-hidden="true" />
                          <DecodedLabel text={route.label} active={index === active} />
                          <span className="hv-menu__go" aria-hidden="true">
                            ›
                          </span>
                        </span>
                        <span className="hv-menu__note" aria-hidden="true">
                          {note}
                        </span>
                      </button>
                    )
                  })}
                </div>
              ))}

              {visible.length === 0 && <p className="hv-menu__empty">{t('common.empty')}</p>}
            </nav>

            {/*
              The preview pane. A route marker, its path strip, one sentence and
              up to three figures — enough to know you are about to go to the
              right place without going there first.
            */}
            {preview && (
              <aside className="hv-menu__preview" data-swapping={swapping} aria-live="polite">
                {/*
                  `key` on the swapping subtree, not on the aside: remounting it
                  is what restarts `hv-draw` and the entry keyframes. Without it
                  React reuses the nodes, the animations are already finished,
                  and the pane changes content with no transition at all.
                */}
                <div className="hv-menu__pv" key={preview.key}>
                  <div className="hv-frame hv-frame--wide">
                    {preview.kicker && <span className="hv-micro">{preview.kicker}</span>}
                    <div className="hv-menu__pv-mark">{preview.mark ?? '—'}</div>
                    {preview.shape && (
                      <svg
                        className="hv-menu__pv-svg"
                        viewBox="0 0 140 100"
                        width="100%"
                        height="120"
                        // Presentation attributes, exactly as the kit sets them.
                        // They are *not* in `menu.css`: without them the shapes
                        // paint as solid black fills and `hv-draw` has no stroke
                        // to draw, so the diagram neither reads nor animates.
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        opacity="0.75"
                        aria-hidden="true"
                        dangerouslySetInnerHTML={{ __html: SHAPES[preview.shape] }}
                      />
                    )}
                    {preview.text && <p className="hv-prose">{preview.text}</p>}
                    {preview.stats && preview.stats.length > 0 && (
                      <>
                        <hr className="hv-hr" />
                        <ul className="hv-leaders">
                          {preview.stats.map(([label, value]) => (
                            <li className="hv-leader" key={label}>
                              <span className="hv-leader__k">{label}</span>
                              <span className="hv-leader__fill" />
                              <span className="hv-leader__v">{value}</span>
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                  </div>
                </div>
              </aside>
            )}
          </div>

          <footer className="hv-menu__foot">
            <span>
              <span className="hv-key">↑</span>
              <span className="hv-key">↓</span> {t('nav.key_move')}
            </span>
            <span>
              <span className="hv-key">↵</span> {t('nav.key_go')}
            </span>
            <span>
              <span className="hv-key">ESC</span> {t('nav.key_close')}
            </span>
          </footer>
        </div>
      )}
    </>
  )
}
