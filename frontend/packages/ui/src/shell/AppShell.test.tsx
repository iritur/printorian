/**
 * The way in, from wherever you happen to be standing.
 *
 * The masthead's right-hand group rendered only for a signed-in actor, so a
 * signed-out visitor's only way in was to leave for a screen that happened to
 * render an `AuthPanel` — the cabinet, the account, the checkout. Reaching one
 * from the catalogue costs the catalogue, which is the exact cost
 * `design/js/auth.js` was written to avoid.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/*
  `ready` is part of the fixture, not a constant, because the state this shell
  got wrong is the one the real provider *starts* in: `actor = null` with
  `ready = false`, before `/auth/me` has answered. A mock that is always ready
  can never reach it.
*/
const session = vi.hoisted(() => ({
  actor: null as { email: string; permissions: string[] } | null,
  ready: true,
}))

vi.mock('../session/session', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('../session/session')
  return {
    ...actual,
    useSession: () => ({
      ready: session.ready,
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

import { AuthPanel } from '../session/AuthPanel'
import { AppShell } from './AppShell'

function shell(children: React.ReactNode = <p>Каталог</p>) {
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
      {children}
    </AppShell>,
  )
}

beforeEach(() => {
  session.actor = null
  session.ready = true
})

/**
 * The state the provider is in for the length of one round trip.
 *
 * `SessionProvider` mounts at `actor = null, ready = false` and only then asks
 * `/auth/me`. Branching on `actor` alone therefore tells a signed-in customer
 * they are signed out until the answer lands — and if they believe it, press
 * «Войти» and start typing, the answer arrives, `actor` turns non-null and
 * `AuthDialog` stands itself down with their input in it. The masthead's
 * right-hand group was empty before there was an answer, and that was right.
 */
describe('before /auth/me has answered', () => {
  it('does not offer a way in it has not established is needed', () => {
    session.ready = false
    shell()

    expect(screen.queryByRole('button', { name: 'Войти' })).not.toBeInTheDocument()
    // Nor the other half: "we have not asked yet" is neither of the two states
    // the masthead draws, and drawing either of them is the invented answer.
    expect(screen.queryByRole('button', { name: 'Выйти' })).not.toBeInTheDocument()
  })

  /*
    The rest of the chrome is not waiting on the session — the shell renders the
    screen underneath it either way, and a masthead that blanked itself for a
    round trip would trade one wrong answer for a flicker.
  */
  it('draws the screen and the chrome regardless', () => {
    session.ready = false
    shell()

    expect(screen.getByText('Каталог')).toBeInTheDocument()
    expect(screen.getByRole('navigation')).toBeInTheDocument()
  })
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

  /*
    The three screens «Войти» is most likely to be pressed on are the three that
    already show the form: the checkout, the cabinet and the account each render
    an inline `AuthPanel` for this exact signed-out state. So the masthead puts a
    second copy of the same form over the first, and the panel's fields have to
    survive being mounted twice — a literal `id` there gives the popup's label a
    control on the page underneath, which the overlay covers and `aria-modal`
    declares inert. `AuthPanel.test.tsx` holds the mechanism; this holds the
    arrangement that produces it, because the shell is what created it.
  */
  it('does not collide with a screen that is already showing the form', async () => {
    shell(<AuthPanel locale="ru" />)

    // Scoped to the masthead on purpose: the panel on the page has «Войти» of
    // its own, on the tab and on the submit, which is the ambiguity this whole
    // arrangement is made of.
    const masthead = screen.getByRole('navigation')
    await userEvent.click(within(masthead).getByRole('button', { name: 'Войти' }))

    const dialog = screen.getByRole('dialog', { name: 'Доступ :: Вход' })
    const label = within(dialog).getByText('Электронная почта') as HTMLLabelElement

    expect(dialog).toContainElement(label.control)

    await userEvent.click(label)
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
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
