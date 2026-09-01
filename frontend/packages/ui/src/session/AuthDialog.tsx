import { useEffect } from 'react'

// Relative, not `@printorian/ui`: this file lives *inside* that package, and
// importing the package from itself is a cycle the bundler resolves by accident.
import type { Locale } from '../i18n/messages'
import { translate } from '../i18n/translate'
import { Modal } from '../shell/Modal'
import { AuthPanel } from './AuthPanel'
import { useSession } from './session'

export interface AuthDialogProps {
  locale: Locale
  /** Dismissed, or signed in — the dialog calls this for both. */
  onClose: () => void
  /** Why this person is being asked to sign in. Passed through to the panel. */
  hint?: string | undefined
  /** Hidden where nobody may self-register — the console admits existing staff. */
  allowRegister?: boolean | undefined
}

/**
 * The kit's `data-auth-open` popup: signing in without losing the page you were
 * on.
 *
 * `design/js/auth.js` states the case and the checkout is the case — navigating
 * away from a configured quote to a sign-in screen loses the quote, and a
 * customer who has just spent five minutes on a model does not get it back by
 * pressing Back. The static kit hung this off any element carrying
 * `[data-auth-open]`; in React the opener is whatever renders the component, so
 * the attribute has nothing left to do and is not emitted.
 *
 * It is the same `AuthPanel` the checkout and the cabinet render inline. That is
 * the point: two sign-in forms would be two places to fix a password rule, and
 * the difference between a panel and a popup is chrome, not identity.
 *
 * **It closes itself once there is a session.** The dialog is opened to get past
 * a wall, so leaving it standing over the page after the wall is gone would make
 * the customer dismiss a form that has already done its job — and on the shell's
 * «Войти» the control that opened it is no longer there to dismiss it with.
 */
export function AuthDialog({ locale, onClose, hint, allowRegister }: AuthDialogProps) {
  const { actor } = useSession()
  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)

  useEffect(() => {
    if (actor) onClose()
  }, [actor, onClose])

  return (
    <Modal
      title={t('auth.title')}
      path="/IDENTITY/AUTHENTICATE"
      /*
        The kit prints the session state in the path strip rather than in the
        body, and it is worth keeping: a popup that says СЕАНС :: НЕ УСТАНОВЛЕН
        is telling you why it appeared, which a bare form does not.
      */
      pathStatus={t('auth.session_none')}
      onClose={onClose}
    >
      <AuthPanel locale={locale} hint={hint} allowRegister={allowRegister} />
    </Modal>
  )
}
