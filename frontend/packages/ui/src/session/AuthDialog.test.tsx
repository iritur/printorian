/**
 * The kit's `data-auth-open` window, and the one thing a popup owes that an
 * inline panel does not.
 *
 * An inline `AuthPanel` disappears with the screen that gated it. A popup does
 * not: it is a separate layer over a page that has, by the time the session
 * exists, stopped needing it — so a dialog that does not stand down leaves a
 * customer dismissing a form that already worked, and on the masthead's «Войти»
 * the control they opened it from is gone.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const session = vi.hoisted(() => ({ actor: null as { email: string } | null }))

vi.mock('./session', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('./session')
  return {
    ...actual,
    useSession: () => ({
      ready: true,
      actor: session.actor,
      signIn: vi.fn(),
      register: vi.fn(),
      signOut: vi.fn(),
    }),
  }
})

import { AuthDialog } from './AuthDialog'

beforeEach(() => {
  session.actor = null
})

describe('standing down', () => {
  it('closes itself once there is a session', () => {
    const onClose = vi.fn()
    const { rerender } = render(<AuthDialog locale="ru" onClose={onClose} />)

    expect(onClose).not.toHaveBeenCalled()

    session.actor = { email: 'buyer@example.com' }
    rerender(<AuthDialog locale="ru" onClose={onClose} />)

    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('the window', () => {
  it('is the shared panel inside the popup chrome, not a second sign-in form', () => {
    render(<AuthDialog locale="ru" onClose={vi.fn()} />)

    expect(screen.getByRole('dialog', { name: 'Доступ :: Вход' })).toBeInTheDocument()
    expect(screen.getByText('СЕАНС :: НЕ УСТАНОВЛЕН')).toBeInTheDocument()
    expect(screen.getByLabelText('Электронная почта')).toBeInTheDocument()
    expect(screen.getByLabelText('Пароль')).toBeInTheDocument()
  })

  it('inherits the popup behaviour rather than re-implementing it', async () => {
    const onClose = vi.fn()
    render(<AuthDialog locale="ru" onClose={onClose} />)

    await userEvent.keyboard('{Escape}')

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('hides registration where nobody may sign themselves up', () => {
    render(<AuthDialog locale="ru" onClose={vi.fn()} allowRegister={false} />)

    expect(screen.queryByRole('button', { name: 'Зарегистрироваться' })).not.toBeInTheDocument()
  })
})
