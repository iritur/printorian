import { useState } from 'react'

import { ApiError } from '@printorian/api-client'

// Relative, not `@printorian/ui`: this file now lives *inside* that package, and
// importing the package from itself is a cycle the bundler resolves by accident.
import type { Locale } from '../i18n/messages'
import { translate, translateError } from '../i18n/translate'
import { useSession } from './session'

export interface AuthPanelProps {
  locale: Locale
  /**
   * Why this person is being asked to sign in.
   *
   * Defaults to the storefront's reason. The console needs its own, because the
   * shared panel otherwise greets an operator at the start of their shift with
   * "sign in to place an order" — copy that made sense while this form existed
   * only at the checkout, and stopped making sense the moment two apps used it.
   */
  hint?: string
  /** Hidden where nobody may self-register — the console admits existing staff. */
  allowRegister?: boolean
}

/**
 * Sign in or create an account, in one small form.
 *
 * Deliberately minimal: a customer meets this at the moment they want to buy
 * something, so anything beyond an email and a password is friction standing
 * between the farm and a paid order.
 *
 * Two tabs rather than two buttons under one form, which is how the kit draws
 * «01 :: Учётная запись». The distinction is not cosmetic: the two paths want
 * different things from the browser — `current-password` against `new-password`
 * for autofill, and a minimum length that only applies to one of them — and a
 * single form cannot declare both.
 */
export function AuthPanel({ locale, hint, allowRegister = true }: AuthPanelProps) {
  const { signIn, register } = useSession()
  const [mode, setMode] = useState<'in' | 'new'>('in')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key)
  const registering = mode === 'new'

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await (registering ? register(email, password) : signIn(email, password))
    } catch (exc: unknown) {
      setError(
        exc instanceof ApiError
          ? translateError(locale, { code: exc.code, details: exc.details })
          : t('error.internal'),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="hv-stack" onSubmit={(event) => void submit(event)}>
      {allowRegister && (
        <div className="hv-seg" role="group" aria-label={t('checkout.need_account')}>
          {(['in', 'new'] as const).map((option) => (
            <button
              key={option}
              type="button"
              className="hv-seg__btn"
              aria-pressed={mode === option}
              // Clearing the error on a tab change, because it describes the
              // attempt the customer has just navigated away from.
              onClick={() => {
                setMode(option)
                setError(null)
              }}
            >
              {option === 'in' ? t('checkout.sign_in') : t('checkout.register')}
            </button>
          ))}
        </div>
      )}

      <div className="hv-field">
        <label className="hv-label" htmlFor="auth-email">
          {t('checkout.email')}
        </label>
        <input
          className="hv-input"
          id="auth-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>

      <div className="hv-field">
        <label className="hv-label" htmlFor="auth-password">
          {t('checkout.password')}
        </label>
        <input
          className="hv-input"
          id="auth-password"
          type="password"
          /*
            The tab decides this. `current-password` on the sign-in path lets a
            manager fill a saved one; `new-password` on the other tells it to
            offer a generated one instead of the password for some other site.
          */
          autoComplete={registering ? 'new-password' : 'current-password'}
          required
          // Only the new account has to meet the length rule. Enforcing it on
          // sign-in would lock out anybody whose password predates the rule.
          {...(registering ? { minLength: 10 } : {})}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {registering && <span className="hv-hint">{t('checkout.password_rule')}</span>}
      </div>

      {error && (
        <p className="hv-hint hv-bad" role="alert">
          {error}
        </p>
      )}

      <div className="hv-row">
        <button className="hv-btn hv-btn--primary" type="submit" disabled={busy}>
          {registering ? t('checkout.register') : t('checkout.sign_in')}
        </button>
        <span className="hv-hint">{hint ?? t('checkout.need_account')}</span>
      </div>
    </form>
  )
}
