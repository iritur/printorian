/**
 * One form, mounted more than once.
 *
 * The panel was written when exactly one of it could be on screen — the
 * checkout, the cabinet, the account, each gating its own page. `AuthDialog`
 * hung off the masthead ended that: on those three screens the shell now offers
 * «Войти» over a page that is already showing the panel, so two copies stand in
 * one document. Field ids written as literals then collide, and a duplicate id
 * is not a tidiness complaint — `<label for>` resolves to the *first* match in
 * tree order, so the popup's label drives the input on the page behind it,
 * which `aria-modal` has just declared inert.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

vi.mock('./session', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('./session')
  return {
    ...actual,
    useSession: () => ({
      ready: true,
      actor: null,
      signIn: vi.fn(),
      register: vi.fn(),
      signOut: vi.fn(),
    }),
  }
})

import { AuthPanel } from './AuthPanel'

/** The control a `<label>` actually drives, as the browser resolves it. */
function controlOf(label: HTMLElement) {
  return (label as HTMLLabelElement).control
}

describe('two panels in one document', () => {
  it('gives every field an id of its own', () => {
    render(
      <>
        <div data-testid="page">
          <AuthPanel locale="ru" />
        </div>
        <div data-testid="popup">
          <AuthPanel locale="ru" />
        </div>
      </>,
    )

    const ids = [...document.querySelectorAll('input')].map((input) => input.id)

    expect(ids).toHaveLength(4)
    expect(new Set(ids).size).toBe(4)
    expect(ids.every((id) => id !== '')).toBe(true)
  })

  it('points each label at the field beside it, not at the first one on the page', async () => {
    render(
      <>
        <div data-testid="page">
          <AuthPanel locale="ru" />
        </div>
        <div data-testid="popup">
          <AuthPanel locale="ru" />
        </div>
      </>,
    )

    const popup = screen.getByTestId('popup')
    const email = within(popup).getByText('Электронная почта')
    const password = within(popup).getByText('Пароль')

    expect(popup).toContainElement(controlOf(email))
    expect(popup).toContainElement(controlOf(password))

    // The failure this is really about: clicking the popup's own label must put
    // the caret in the popup, not in a field the overlay is covering.
    await userEvent.click(email)
    expect(popup).toContainElement(document.activeElement as HTMLElement)
  })
})
