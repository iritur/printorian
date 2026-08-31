/**
 * The way in, from wherever you happen to be standing.
 *
 * The masthead's right-hand group rendered only for a signed-in actor, so a
 * signed-out visitor's only way in was to leave for a screen that happened to
 * render an `AuthPanel` — the cabinet, the account, the checkout. Reaching one
 * from the catalogue costs the catalogue, which is the exact cost
 * `design/js/auth.js` was written to avoid.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const session = vi.hoisted(() => ({ actor: null as { email: string; permissions: string[] } | null }))

vi.mock('../session/session', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('../session/session')
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

// The health strip asks the API on mount. It has its own tests; here it would
// only be a fetch nobody is asserting on.
vi.mock('./useHealth', () => ({ useHealth: () => ({ status: 'ONLINE' }) }))

import { AppShell } from './AppShell'

function shell() {
  render(
    <AppShell
      locale="ru"
      onLocaleChange={vi.fn()}
      realm="public"
      tab="Каталог :: Витрина"
      path="/CATALOG"
      routes={[]}
      current="catalog"
      onNavigate={vi.fn()}
      statusNote="PRINTORIAN"
    >
      <p>Каталог</p>
    </AppShell>,
  )
}

beforeEach(() => {
  session.actor = null
})

describe('signed out', () => {
  it('offers a way in from the masthead', () => {
    shell()

    expect(screen.getByRole('button', { name: 'Войти' })).toBeInTheDocument()
  })

  it('signs you in over the page rather than instead of it', async () => {
    shell()

    await userEvent.click(screen.getByRole('button', { name: 'Войти' }))

    expect(screen.getByRole('dialog', { name: 'Доступ :: Вход' })).toBeInTheDocument()
    // The screen underneath is still mounted — which is the whole argument for a
    // popup over a navigation to a sign-in page.
    expect(screen.getByText('Каталог')).toBeInTheDocument()
  })
})

describe('signed in', () => {
  it('shows who you are instead of the way in', () => {
    session.actor = { email: 'buyer@example.com', permissions: [] }
    shell()

    expect(screen.getByText('buyer@example.com')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Выйти' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Войти' })).not.toBeInTheDocument()
  })
})
