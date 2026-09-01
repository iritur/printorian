/**
 * What makes a tab strip a tab strip rather than a row of buttons.
 *
 * The markup is the easy half and the kit already carries it. This is the half
 * each unbuilt screen would have re-derived: one stop in the page's tab order
 * for the whole strip, arrow keys to move inside it, and an axis that matches
 * the way the control is drawn.
 */

import { useState } from 'react'

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { TabRail } from './TabRail'
import { Tabs } from './Tabs'

const TABS = [
  { key: 'now', label: 'Сейчас' },
  { key: 'spec', label: 'Параметры' },
  { key: 'svc', label: 'Обслуживание' },
] as const

type Key = (typeof TABS)[number]['key']

function Strip({ start = 'now' }: { start?: Key }) {
  const [current, setCurrent] = useState<Key>(start)
  return <Tabs tabs={TABS} current={current} onSelect={setCurrent} label="Принтер" />
}

function Rail({ start = 'now' }: { start?: Key }) {
  const [current, setCurrent] = useState<Key>(start)
  return <TabRail tabs={TABS} current={current} onSelect={setCurrent} label="Разделы" />
}

describe('the tab order', () => {
  it('costs one Tab press for the whole strip, not one per tab', async () => {
    render(
      <>
        <Strip />
        <button type="button">За полосой</button>
      </>,
    )

    await userEvent.tab()
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Сейчас' }))

    // The next stop is past the strip. Three tabs would be three presses, and
    // the settings rail's fourteen sections would be fourteen — which is the
    // whole reason the tablist pattern exists.
    await userEvent.tab()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'За полосой' }))
  })

  it('moves the single stop to whichever tab is selected', async () => {
    render(<Strip start="svc" />)

    await userEvent.tab()

    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Обслуживание' }))
  })
})

describe('arrow keys on the strip', () => {
  it('moves selection and focus together, so the panel follows', async () => {
    render(<Strip />)
    screen.getByRole('tab', { name: 'Сейчас' }).focus()

    await userEvent.keyboard('{ArrowRight}')

    const spec = screen.getByRole('tab', { name: 'Параметры' })
    expect(document.activeElement).toBe(spec)
    expect(spec).toHaveAttribute('aria-selected', 'true')
  })

  it('wraps round the end rather than stopping dead', async () => {
    render(<Strip start="svc" />)
    screen.getByRole('tab', { name: 'Обслуживание' }).focus()

    await userEvent.keyboard('{ArrowRight}')

    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Сейчас' }))
  })

  it('jumps to the ends on Home and End', async () => {
    render(<Strip start="spec" />)
    screen.getByRole('tab', { name: 'Параметры' }).focus()

    await userEvent.keyboard('{End}')
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Обслуживание' }))

    await userEvent.keyboard('{Home}')
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Сейчас' }))
  })

  it('ignores the axis it is not drawn on', async () => {
    render(<Strip />)
    screen.getByRole('tab', { name: 'Сейчас' }).focus()

    // A horizontal strip that answered ArrowDown would swallow the page scroll
    // of anyone who happened to be focused on it.
    await userEvent.keyboard('{ArrowDown}')

    expect(screen.getByRole('tab', { name: 'Сейчас' })).toHaveAttribute('aria-selected', 'true')
  })
})

describe('the rail is the same control on its side', () => {
  it('moves on ArrowDown and leaves ArrowRight alone', async () => {
    render(<Rail />)
    screen.getByRole('tab', { name: /Сейчас/ }).focus()

    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: /Сейчас/ })).toHaveAttribute('aria-selected', 'true')

    await userEvent.keyboard('{ArrowDown}')
    expect(screen.getByRole('tab', { name: /Параметры/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('tells a screen reader which way it runs', () => {
    render(<Rail />)

    expect(screen.getByRole('tablist', { name: 'Разделы' })).toHaveAttribute(
      'aria-orientation',
      'vertical',
    )
  })
})
