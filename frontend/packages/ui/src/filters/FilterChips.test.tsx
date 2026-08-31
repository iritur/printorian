/**
 * The chip row's three decisions, each of which a hand-rolled copy had got
 * differently.
 *
 * Before this component there were three of them — the console's table tags, the
 * account's order groups and the journal's sections. Two let you clear the
 * filter by pressing the chip already in force and one did not; one printed «—»
 * for a total it had not received and printed `0` for every section beside it,
 * in the same row.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { FilterChips } from './FilterChips'

const CHIPS = [
  { key: 'active', label: 'В работе', count: 2, tone: 'live' as const },
  { key: 'done', label: 'Завершены', count: 11, tone: 'good' as const },
]

function draw(active: string | null, onSelect = vi.fn(), all: { label: string; count: number | null } = { label: 'Все', count: 14 }) {
  render(<FilterChips label="Фильтр" all={all} chips={CHIPS} active={active} onSelect={onSelect} />)
  return onSelect
}

describe('ADR-0007 on a count', () => {
  it('renders an em dash for a count nobody has taken, not a zero', () => {
    render(
      <FilterChips
        label="Фильтр"
        all={{ label: 'Все', count: null }}
        chips={[{ key: 'cost', label: 'Себестоимость', count: null }]}
        active={null}
        onSelect={vi.fn()}
      />,
    )

    // Both chips, because the failure this catches is a row where the total
    // said «—» and every slice beside it said `0` — one line making two
    // different promises about the same missing answer.
    expect(screen.getByRole('button', { name: /Все\s*—/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Себестоимость\s*—/ })).toBeInTheDocument()
    expect(screen.queryByText('0')).not.toBeInTheDocument()
  })

  it('renders a measured zero as zero, because "none" is an answer', () => {
    render(
      <FilterChips
        label="Фильтр"
        chips={[{ key: 'cancelled', label: 'Отменены', count: 0 }]}
        active={null}
        onSelect={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: /Отменены\s*0/ })).toBeInTheDocument()
  })
})

describe('clearing', () => {
  it('clears the filter when the chip already in force is pressed', async () => {
    const onSelect = draw('active')

    await userEvent.click(screen.getByRole('button', { name: /В работе\s*2/ }))

    // `null`, not `'active'` again: a filter you cannot turn off is a trap, and
    // on a long list the «Все» chip has scrolled out of sight.
    expect(onSelect).toHaveBeenCalledWith(null)
  })

  it('selects a chip that is not in force', async () => {
    const onSelect = draw('active')

    await userEvent.click(screen.getByRole('button', { name: /Завершены\s*11/ }))

    expect(onSelect).toHaveBeenCalledWith('done')
  })

  it('reports the «Все» chip as no filter at all', async () => {
    const onSelect = draw('done')

    await userEvent.click(screen.getByRole('button', { name: /Все\s*14/ }))

    expect(onSelect).toHaveBeenCalledWith(null)
  })
})

describe('what the row says about itself', () => {
  it('presses «Все» exactly when nothing is filtered', () => {
    draw(null)

    expect(screen.getByRole('button', { name: /Все\s*14/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /В работе\s*2/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('tones the chip, so the number carries the signal Harvester colours', () => {
    draw(null)

    expect(screen.getByRole('button', { name: /В работе\s*2/ })).toHaveAttribute(
      'data-tone',
      'live',
    )
    // An untoned chip is neutral rather than absent: `.hv-tag[data-tone]` is how
    // the stylesheet reaches the count at all.
    expect(screen.getByRole('button', { name: /Все\s*14/ })).toHaveAttribute('data-tone', 'neutral')
  })
})
