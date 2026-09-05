/**
 * The zone tariff editor, on the claims a glance cannot check.
 *
 * The prefix field carries most of them: it is the only control here that holds
 * state of its own, and the reason it does is a bug that a screenshot cannot
 * show — a comma that vanishes the instant it is typed.
 */

import { useState } from 'react'

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ZoneRow } from './ZonesEditor'
import { ZonesEditor } from './ZonesEditor'

function rows(): ZoneRow[] {
  return [
    {
      code: 'msk',
      base: '400',
      per_kg: '0',
      transit_days: 1,
      postcode_prefixes: ['101', '1'],
      enabled: true,
    },
    {
      code: 'intl',
      base: '4000',
      per_kg: '250',
      transit_days: 14,
      postcode_prefixes: ['9'],
      enabled: false,
    },
  ]
}

/**
 * Render the editor with something holding its rows, the way `SettingsPage`
 * does.
 *
 * The state is not decoration: every input here is controlled from the `rows`
 * prop, so an editor rendered against a fixed array cannot be typed into at all
 * — React puts the old value back on every keystroke. A test that skipped this
 * would be testing a component nobody uses.
 */
function draw(overrides: { dirty?: boolean } = {}) {
  const onChange = vi.fn()
  const onRevert = vi.fn()

  function Harness() {
    const [current, setCurrent] = useState<ZoneRow[]>(rows())
    return (
      <ZonesEditor
        locale="ru"
        rows={current}
        dirty={overrides.dirty ?? false}
        onChange={(next) => {
          onChange(next)
          setCurrent(next)
        }}
        onRevert={onRevert}
        name="Зоны и тарифы"
        hint=""
        revertLabel="Вернуть"
      />
    )
  }

  render(<Harness />)
  return { onChange, onRevert }
}

describe('the zone tariff editor', () => {
  it('draws one row per zone with the columns the kit specifies', () => {
    draw()

    for (const column of ['Зона', 'Индексы', 'Базовая', 'За кг', 'Срок']) {
      expect(screen.getByText(column)).toBeInTheDocument()
    }
    expect(screen.getByDisplayValue('msk')).toBeInTheDocument()
    expect(screen.getByDisplayValue('intl')).toBeInTheDocument()
    expect(screen.getByText('ЗОНА ОПРЕДЕЛЯЕТСЯ ПО ИНДЕКСУ')).toBeInTheDocument()
  })

  it('does not draw a shipment count it cannot measure', () => {
    // The kit's «Отправлений» column. Nothing counts parcels yet, and a column
    // of noughts would say the farm has shipped nothing to Moscow (ADR-0007).
    draw()
    expect(screen.queryByText('Отправлений')).not.toBeInTheDocument()
  })

  it('greys a switched-off zone rather than hiding it', () => {
    draw()
    expect(screen.getByDisplayValue('intl').closest('tr')).toHaveClass('hv-faint')
    expect(screen.getByDisplayValue('msk').closest('tr')).not.toHaveClass('hv-faint')
  })

  it('reports a changed rate as a whole new row set', async () => {
    const { onChange } = draw()

    const base = screen.getByLabelText(/Базовая\s*1/)
    await userEvent.clear(base)
    await userEvent.type(base, '5')

    expect(onChange).toHaveBeenCalled()
    const last = onChange.mock.calls.at(-1)?.[0] as ZoneRow[]
    expect(last[0].base).toBe('5')
    // The untouched row travels through unchanged; an editor that rebuilt the
    // whole table from its inputs would quietly normalise the others.
    expect(last[1]).toEqual(rows()[1])
  })

  it('keeps the separator while a prefix list is being extended', async () => {
    // Deriving the input's value from the parsed array eats the comma as it is
    // typed, and the list can then never be extended. This is that test.
    const { onChange } = draw()

    await userEvent.type(screen.getByLabelText(/Индексы\s*1/), ',')

    expect(screen.getByDisplayValue('101, 1,')).toBeInTheDocument()
    // A trailing comma is a half-finished edit, not a blank prefix — a blank one
    // would match every postcode on earth.
    expect((onChange.mock.calls.at(-1)?.[0] as ZoneRow[])[0].postcode_prefixes).toEqual([
      '101',
      '1',
    ])
  })

  it('adds a prefix once it is typed', async () => {
    const { onChange } = draw()

    await userEvent.type(screen.getByLabelText(/Индексы\s*1/), ', 3')

    expect((onChange.mock.calls.at(-1)?.[0] as ZoneRow[])[0].postcode_prefixes).toEqual([
      '101',
      '1',
      '3',
    ])
  })

  it('adds and removes rows', async () => {
    const { onChange } = draw()

    await userEvent.click(screen.getByRole('button', { name: 'Добавить зону' }))
    expect((onChange.mock.calls.at(-1)?.[0] as ZoneRow[]).length).toBe(3)

    await userEvent.click(screen.getAllByRole('button', { name: 'Удалить' })[1])
    const remaining = onChange.mock.calls.at(-1)?.[0] as ZoneRow[]
    expect(remaining.map((row) => row.code)).toEqual(['msk'])
  })

  it('offers the revert only once something has changed', async () => {
    const { onRevert } = draw({ dirty: true })

    await userEvent.click(screen.getByRole('button', { name: 'Вернуть' }))

    expect(onRevert).toHaveBeenCalled()
  })

  it('hides the revert while the row matches what the server sent', () => {
    draw()
    expect(screen.queryByRole('button', { name: 'Вернуть' })).not.toBeInTheDocument()
  })
})
