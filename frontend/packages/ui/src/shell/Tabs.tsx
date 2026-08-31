import { tablistKeyDown } from './tablist'

/**
 * The kit's horizontal tab strip — `.hv-tabs` with `.hv-tabs__btn`.
 *
 * The rail in `TabRail` is the same convention standing up: a rail for a long
 * list down the side of a screen (the account's seven sections, settings'
 * fourteen), a strip for the handful of faces one *thing* has — the printer
 * popup's «Сейчас · Параметры · Обслуживание · Слоты AMS» in `design/fleet.html`.
 * Both switch a `TabView`, which is the panel and its entry animation.
 *
 * **Built before the screens that need it, on purpose.** Nothing imports this
 * yet. The screens the kit draws with a strip — the purchase order and the
 * shipment, and every detail popup after them — are unbuilt, and the whole
 * argument for porting the convention now is that four screens each inventing
 * their own tab is the failure ROADMAP names under "Management tables are not a
 * phase". The keyboard behaviour below is the part each of those four would have
 * got slightly differently, or not at all.
 *
 * No CSS ships with it. `harvester/system.css` already styles `.hv-tabs` and
 * `.hv-tabs__btn`, including the underline on `[aria-selected='true']`, so this
 * is markup and behaviour over a stylesheet the kit port already paid for.
 */

export interface TabsProps<Key extends string> {
  tabs: readonly { key: Key; label: string }[]
  current: Key
  onSelect: (key: Key) => void
  /** Accessible name for the strip. */
  label: string
}

export function Tabs<Key extends string>({ tabs, current, onSelect, label }: TabsProps<Key>) {
  return (
    <div
      className="hv-tabs"
      role="tablist"
      aria-label={label}
      onKeyDown={(event) =>
        tablistKeyDown(
          event,
          tabs.map((tab) => tab.key),
          onSelect,
          'horizontal',
        )
      }
    >
      {tabs.map((tab) => (
        <button
          key={tab.key}
          className="hv-tabs__btn"
          type="button"
          role="tab"
          aria-selected={tab.key === current}
          /*
            One stop in the page's tab order for the whole strip, not one per
            tab. Without this, reaching the content past a four-tab strip costs
            four Tab presses and past the settings rail fourteen — which is the
            reason the tablist pattern exists at all.
          */
          tabIndex={tab.key === current ? 0 : -1}
          onClick={() => onSelect(tab.key)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
