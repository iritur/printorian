/**
 * The two territories the system is divided into.
 *
 *   public   ВИТРИНА — what a customer sees. A product.
 *   control  ПУЛЬТ   — what the farm runs on. An instrument.
 *
 * The value lands on `<html data-realm>`, where `realm.css` reads it. It is one
 * attribute rather than a class per screen because the split is a property of
 * the whole console: every screen in a bundle is on the same side, and a screen
 * that could change its own realm would be a screen that can lie about which
 * one it is.
 */
export type Realm = 'public' | 'control'

export const REALM_LABEL: Record<Realm, string> = {
  public: 'Витрина',
  control: 'Пульт',
}

export const OTHER_REALM: Record<Realm, Realm> = {
  public: 'control',
  control: 'public',
}

/**
 * Put the realm on the document element.
 *
 * On `<html>` rather than on the React root, because the hazard rail is drawn
 * on `body::before` and fixed to the viewport — it has to sit outside anything
 * React owns, or it would scroll away with the content it is meant to frame.
 */
export function applyRealm(realm: Realm): void {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.realm = realm
}
