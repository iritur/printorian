/**
 * The popup's behaviour, which is the whole reason it is a component.
 *
 * The markup was already being copied between screens; what was not copied was
 * everything here. Each of these is a way a hand-rolled modal goes wrong:
 *
 * * it traps you, because nobody wired Esc;
 * * it closes while you are selecting text, because the backdrop listened to
 *   any click that ended on it;
 * * it drops keyboard focus on `<body>`, so closing sends you back to the top
 *   of the page;
 * * it says `aria-modal` and then lets Tab walk out of it, which makes the
 *   promise false for exactly the people relying on it.
 */

import React from 'react'

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { Modal } from './Modal'

function open(onClose = vi.fn(), extra?: React.ReactNode) {
  render(
    <Modal
      title="Новый принтер :: Парк"
      meta={[{ label: 'РЕЖИМ', value: 'СОЗДАНИЕ' }]}
      path="/FLEET/PRINTERS/NEW"
      onClose={onClose}
      footer={<button type="button">Сохранить</button>}
    >
      <label>
        Название
        <input />
      </label>
      <label>
        Модель
        <input />
      </label>
      {extra}
    </Modal>,
  )
  return onClose
}

describe('closing', () => {
  it('closes on Escape', async () => {
    const onClose = open()

    await userEvent.keyboard('{Escape}')

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closes when the backdrop is clicked', async () => {
    const onClose = open()

    const overlay = document.querySelector('.hv-overlay') as HTMLElement
    await userEvent.click(overlay)

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('stays open when a drag from inside merely ends on the backdrop', () => {
    const onClose = open()
    const overlay = document.querySelector('.hv-overlay') as HTMLElement
    const field = screen.getByLabelText('Название')

    // Selecting the contents of a field and releasing past the edge of the
    // dialog is a text selection, not a dismissal — and closing on it throws
    // away whatever was being typed.
    field.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    overlay.dispatchEvent(new MouseEvent('click', { bubbles: true }))

    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes from the ✕', async () => {
    const onClose = open()

    await userEvent.click(screen.getByRole('button', { name: 'Закрыть' }))

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not close on a click inside', async () => {
    const onClose = open()

    await userEvent.click(screen.getByLabelText('Название'))

    expect(onClose).not.toHaveBeenCalled()
  })
})

describe('focus', () => {
  it('puts focus on the first control, which is the field about to be typed in', () => {
    open()

    expect(document.activeElement).toBe(screen.getByLabelText('Название'))
  })

  it('gives focus back to whatever opened it', async () => {
    const onClose = vi.fn()
    function Harness() {
      const [open, setOpen] = React.useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Добавить принтер
          </button>
          {open && (
            <Modal
              title="Новый принтер"
              onClose={() => {
                setOpen(false)
                onClose()
              }}
            >
              <input aria-label="Название" />
            </Modal>
          )}
        </>
      )
    }
    render(<Harness />)
    const opener = screen.getByRole('button', { name: 'Добавить принтер' })
    await userEvent.click(opener)
    expect(document.activeElement).toBe(screen.getByLabelText('Название'))

    await userEvent.keyboard('{Escape}')

    // Not `<body>`: a keyboard user who closes a popup should be back on the
    // control they opened it from, not at the top of the page.
    expect(document.activeElement).toBe(opener)
  })

  it('keeps Tab inside the dialog', async () => {
    open()
    const dialog = document.querySelector('.hv-modal') as HTMLElement
    screen.getByRole('button', { name: 'Сохранить' }).focus()

    await userEvent.tab()

    // Containment is the invariant worth asserting, not which element comes
    // round first: the ordering is the chrome's business and may change, while
    // "Tab never leaves a dialog that claims the page is inert" may not.
    expect(dialog.contains(document.activeElement)).toBe(true)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Закрыть' }))
  })

  it('wraps backwards from the first control', async () => {
    open()
    const dialog = document.querySelector('.hv-modal') as HTMLElement
    screen.getByRole('button', { name: 'Закрыть' }).focus()

    await userEvent.tab({ shift: true })

    expect(dialog.contains(document.activeElement)).toBe(true)
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Сохранить' }))
  })
})

describe('the page behind', () => {
  it('does not scroll while the popup is open, and scrolls again after', () => {
    const { unmount } = render(
      <Modal title="Новый принтер" onClose={vi.fn()}>
        <input aria-label="Название" />
      </Modal>,
    )

    expect(document.body.style.overflow).toBe('hidden')

    unmount()

    expect(document.body.style.overflow).not.toBe('hidden')
  })
})

describe('the chrome', () => {
  it('carries the identifiers a support conversation needs', () => {
    open()

    expect(screen.getByText('Новый принтер :: Парк')).toBeInTheDocument()
    expect(screen.getByText('СОЗДАНИЕ')).toBeInTheDocument()
    expect(screen.getByText('/FLEET/PRINTERS/NEW')).toBeInTheDocument()
  })
})
