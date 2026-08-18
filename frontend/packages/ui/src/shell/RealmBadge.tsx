import type { Realm } from './realm'
import { OTHER_REALM, REALM_LABEL } from './realm'

export interface RealmBadgeProps {
  realm: Realm
  /**
   * Open the navigation overlay already filtered to the *other* territory.
   *
   * The badge says which side you are standing on, and the only reason anyone
   * looks at it is to cross — so the click does the crossing rather than merely
   * restating what the rail already says.
   */
  onCross: (to: Realm) => void
}

/**
 * The chip in the app bar naming the realm this screen belongs to.
 *
 * Its flag is hatched under `control` and solid under `public`, from
 * `realm.css` — the same texture as the hazard rail, so the two read as one
 * object rather than two unrelated marks.
 *
 * Deliberately not colour-coded. A coloured pixel means machine state
 * everywhere in this system, and spending `live`/`warn`/`bad` on "which realm
 * is this" would make every screen lie a little.
 */
export function RealmBadge({ realm, onCross }: RealmBadgeProps) {
  const other = OTHER_REALM[realm]

  return (
    <button
      type="button"
      className="hv-realm"
      onClick={() => onCross(other)}
      title={`${REALM_LABEL[realm]} — перейти в «${REALM_LABEL[other]}»`}
    >
      <i className="hv-realm__flag" aria-hidden="true" />
      {REALM_LABEL[realm]}
    </button>
  )
}
