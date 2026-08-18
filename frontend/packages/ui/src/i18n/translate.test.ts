import { describe, expect, it } from 'vitest'

import { en, ru } from './messages'
import { createTranslator, translateError } from './translate'

describe('message catalogues', () => {
  it('cover exactly the same keys in both locales', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(ru).sort())
  })

  it('has no empty messages', () => {
    for (const [key, value] of [...Object.entries(ru), ...Object.entries(en)]) {
      expect(value, key).not.toBe('')
    }
  })
})

describe('translate', () => {
  it('returns the message for the active locale', () => {
    expect(createTranslator('ru').t('common.all')).toBe('Все')
    expect(createTranslator('en').t('common.all')).toBe('All')
  })

  it('interpolates details from an API error body', () => {
    // Placeholders are only substituted when the catalogue uses them; the point
    // is that `details` is passed through untouched from the backend.
    const rendered = createTranslator('en').tError({
      code: 'error.identity.password_too_short',
      details: { minimum: 10 },
    })
    expect(rendered).toBe('That password is too short')
  })
})

describe('translateError', () => {
  it('renders a known code', () => {
    expect(translateError('ru', { code: 'error.identity.email_taken' })).toBe(
      'Эта почта уже зарегистрирована',
    )
  })

  it('falls back to the nearest known prefix for an unseen code', () => {
    // A driver error code added by a future brand adapter must still render.
    expect(translateError('en', { code: 'error.driver.some_new_failure' })).toBe(
      'Printer communication error',
    )
  })

  it('falls back to the internal error for a wholly unknown code', () => {
    expect(translateError('en', { code: 'totally.unknown' })).toBe('Internal error')
  })

  it('covers every error code the backend can currently emit', () => {
    // Kept in step with printorian/core/errors.py and the driver error taxonomy.
    const backendCodes = [
      'error.validation',
      'error.not_found',
      'error.conflict',
      'error.permission_denied',
      'error.unauthenticated',
      'error.domain_rule',
      'error.integration',
      'error.configuration',
      'error.internal',
      'error.identity.invalid_credentials',
      'error.identity.email_taken',
      'error.identity.session_expired',
      'error.identity.account_disabled',
      'error.driver.unavailable',
      'error.driver.rejected',
      'error.driver.unknown_brand',
    ]
    for (const code of backendCodes) {
      expect(code in ru, code).toBe(true)
    }
  })
})

describe('request-validation codes', () => {
  // The backend sends `error.validation.<field>.<rule>`, so the prefix fallback
  // has to carry anything not spelled out. Before this existed, every rejected
  // form field reached the user as "internal error".
  it('renders the constraint a person can act on', () => {
    expect(
      translateError('ru', {
        code: 'error.validation.password.string_too_short',
        details: { field: 'password', limit: 10 },
      }),
    ).toBe('Пароль должен быть не короче 10 символов')

    expect(
      translateError('en', {
        code: 'error.validation.password.string_too_short',
        details: { field: 'password', limit: 10 },
      }),
    ).toBe('Password must be at least 10 characters')
  })

  it('falls back to the field when the rule is not spelled out', () => {
    expect(translateError('en', { code: 'error.validation.email.value_error' })).toBe(
      'Check the email address',
    )
  })

  it('falls back to the generic message for a field it has never heard of', () => {
    expect(translateError('en', { code: 'error.validation.nozzle_diameter_mm.less_than' })).toBe(
      'Check the values you entered',
    )
  })

  it('never renders an internal error for a validation failure', () => {
    // The symptom that started this: a short password told the user the server
    // had broken.
    for (const code of [
      'error.validation.password.string_too_short',
      'error.validation.email.missing',
      'error.validation.display_name.string_too_short',
    ]) {
      expect(translateError('ru', { code, details: { limit: 10 } })).not.toBe(
        ru['error.internal'],
      )
    }
  })
})
