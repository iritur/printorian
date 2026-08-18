import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DeltaPreview } from './DeltaPreview'
import { PriceBreakdown } from './PriceBreakdown'
import { formatBasis, formatChange, formatMoney } from './format'
import type { Basis, Breakdown, Delta } from './format'

const basis = (overrides: Partial<Basis>): Basis => ({
  kind: 'flat',
  quantity: null,
  unit: null,
  rate: null,
  percent: null,
  of_codes: [],
  tier_min_quantity: null,
  ...overrides,
})

const BREAKDOWN: Breakdown = {
  currency: 'RUB',
  quantity: 10,
  total: '8174.79',
  unit_price: '817.48',
  by_category: {},
  // The real payload always carries one, and the foot prints it: the id is how a
  // quote stays reproducible after the farm changes its rates (ADR-0020).
  rate_snapshot_id: 'rates_8f41c2',
  lines: [
    {
      code: 'material.filament',
      category: 'material',
      amount: '2880.00',
      basis: basis({ kind: 'rate_over_quantity', quantity: '1200', unit: 'gram', rate: '2.40' }),
    },
    {
      code: 'labor.setup',
      category: 'labor',
      amount: '150.00',
      basis: basis({ kind: 'rate_over_quantity', quantity: '0.25', unit: 'hour', rate: '600' }),
    },
    {
      code: 'logistics.shipping',
      category: 'logistics',
      amount: '400.00',
      basis: basis({ kind: 'flat', rate: '400' }),
    },
    {
      code: 'adjustment.volume_discount',
      category: 'adjustment',
      amount: '-399.57',
      basis: basis({ kind: 'tiered_percent', percent: '5.00', tier_min_quantity: 10 }),
    },
  ],
}

const DELTA: Delta = {
  currency: 'RUB',
  comparable: true,
  total_before: '1530.71',
  total_after: '1878.46',
  total_change: '347.75',
  unit_before: '812.40',
  unit_after: '1160.15',
  unit_change: '347.75',
  changed: [
    {
      code: 'postprocess.painted',
      category: 'labor',
      before: '0.00',
      after: '1150.00',
      change: '1150.00',
      is_new: true,
      is_removed: false,
    },
    {
      code: 'material.filament',
      category: 'material',
      before: '288.00',
      after: '144.00',
      change: '-144.00',
      is_new: false,
      is_removed: false,
    },
  ],
}

describe('money formatting', () => {
  it('formats in the reader’s locale', () => {
    // Non-breaking spaces vary by runtime; compare on the digits that matter.
    expect(formatMoney('1530.71', 'RUB', 'ru')).toMatch(/1\D?530,71/)
    expect(formatMoney('1530.71', 'RUB', 'en')).toMatch(/1,530\.71/)
  })

  it('always signs a change so the direction is unmistakable', () => {
    expect(formatChange('347.75', 'RUB', 'en')).toMatch(/^\+/)
    expect(formatChange('-144.00', 'RUB', 'en')).toMatch(/-/)
  })

  it('never mangles an unparseable amount into NaN', () => {
    expect(formatMoney('not-a-number', 'RUB', 'en')).toBe('not-a-number RUB')
  })
})

describe('basis rendering', () => {
  it('explains a rate over a quantity', () => {
    const text = formatBasis(
      basis({ kind: 'rate_over_quantity', quantity: '4.2', unit: 'hour', rate: '600' }),
      'RUB',
      'ru',
    )
    expect(text).toContain('4,2')
    expect(text).toContain('ч')
    expect(text).toContain('×')
  })

  it('translates the unit per locale', () => {
    const args = basis({ kind: 'rate_over_quantity', quantity: '100', unit: 'gram', rate: '2.4' })
    expect(formatBasis(args, 'RUB', 'ru')).toContain('г')
    expect(formatBasis(args, 'RUB', 'en')).toContain('g')
  })

  it('shows a tier threshold alongside its percent', () => {
    const text = formatBasis(
      basis({ kind: 'tiered_percent', percent: '5', tier_min_quantity: 10 }),
      'RUB',
      'en',
    )
    expect(text).toContain('5%')
    expect(text).toContain('10')
  })

  it('returns nothing rather than a stray dash when the basis says nothing', () => {
    expect(formatBasis(basis({ kind: 'flat' }), 'RUB', 'en')).toBe('')
    expect(formatBasis(basis({ kind: 'percent_of' }), 'RUB', 'en')).toBe('')
  })
})

describe('PriceBreakdown', () => {
  /**
   * Line labels only, not the group headings above them.
   *
   * Both exist and one pair genuinely collides: the kit heads the first group
   * «Материал» and its first line is also «Материал», so an unscoped query matches
   * two nodes. Scoping to the leader key is what makes the assertion about the
   * line rather than about whichever of the two the query happened to find.
   */
  const labels = (container: HTMLElement) =>
    [...container.querySelectorAll('.hv-leader__k')].map(
      (node) => node.firstChild?.textContent?.trim(),
    )

  it('lists every line with a label and an amount', () => {
    const { container } = render(<PriceBreakdown breakdown={BREAKDOWN} locale="ru" />)

    expect(labels(container)).toEqual(
      expect.arrayContaining(['Материал', 'Труд · подготовка задания', 'Доставка']),
    )
  })

  it('groups the lines under the headings the kit prints', () => {
    const { container } = render(<PriceBreakdown breakdown={BREAKDOWN} locale="ru" />)

    // Five headings over eight categories: a customer reading a dozen lines needs
    // them sorted into the questions they are actually asking.
    const headings = [...container.querySelectorAll('.hv-label')].map(
      (node) => node.textContent,
    )
    expect(headings).toContain('Материал')
    expect(headings).toContain('Труд')
    expect(headings).toContain('Логистика · Накладные · Риски')
  })

  it('names what it was priced against', () => {
    const { container } = render(
      <PriceBreakdown breakdown={BREAKDOWN} locale="ru" promisedHours="74" />,
    )

    // The snapshot id is what makes «the price is held» checkable rather than a
    // claim — it is the same value the order keeps (ADR-0020).
    const foot = container.querySelector('.hv-panel__foot')!
    expect(foot.textContent).toContain('74')
    // Abbreviated for the foot, exactly as the kit prints it...
    expect(foot.textContent).toContain('SNAP.8F41C2')
    // ...with the full digest still on the element, so nothing is actually lost.
    const rates = foot.querySelector('[title]')!
    expect(rates.getAttribute('title')).toBe(BREAKDOWN.rate_snapshot_id)
  })

  it('omits the lead time when nobody supplied one', () => {
    const { container } = render(<PriceBreakdown breakdown={BREAKDOWN} locale="ru" />)

    // A breakdown carries money, not hours (ADR-0002). Absent beats invented.
    expect(container.querySelector('.hv-panel__foot')!.textContent).not.toContain('СРОК')
  })

  it('explains how each line was computed', () => {
    render(<PriceBreakdown breakdown={BREAKDOWN} locale="ru" />)
    const items = screen.getAllByRole('listitem')

    // The material line shows grams × rate, not just a number.
    expect(within(items[0]!).getByText(/×/)).toBeDefined()
  })

  it('marks credits so a discount is not just a small minus sign', () => {
    const { container } = render(<PriceBreakdown breakdown={BREAKDOWN} locale="ru" />)
    const credits = container.querySelectorAll('[data-credit="true"]')
    expect(credits).toHaveLength(1)
  })

  it('shows the per-item price only when more than one is ordered', () => {
    render(<PriceBreakdown breakdown={BREAKDOWN} locale="en" />)
    expect(screen.getByText('Price per item')).toBeDefined()

    const single = { ...BREAKDOWN, quantity: 1 }
    render(<PriceBreakdown breakdown={single} locale="en" />)
    expect(screen.getAllByText('Price per item')).toHaveLength(1) // still only the first
  })

  it('renders in English too', () => {
    const { container } = render(<PriceBreakdown breakdown={BREAKDOWN} locale="en" />)
    expect(labels(container)).toEqual(
      expect.arrayContaining(['Material', 'Volume discount']),
    )
  })
})

describe('DeltaPreview', () => {
  it('shows each changed line with its direction', () => {
    const { container } = render(<DeltaPreview delta={DELTA} locale="ru" />)

    expect(screen.getByText('Окраска')).toBeDefined()

    // Rises before falls, and each figure carries its direction — that ordering
    // is what lets a customer read the cost of an option without doing sums.
    const lines = [...container.querySelectorAll('.hv-leader[data-direction]')]
    expect(lines).toHaveLength(2)
    expect(lines[0]!.getAttribute('data-direction')).toBe('up')
    expect(lines[0]!.querySelector('.hv-leader__v')!.textContent).toMatch(/^\+/)
    expect(lines[1]!.getAttribute('data-direction')).toBe('down')
    expect(lines[1]!.querySelector('.hv-leader__v')!.textContent).toMatch(/-/)
  })

  it('flags a newly added line', () => {
    render(<DeltaPreview delta={DELTA} locale="ru" />)
    expect(screen.getByText('новая позиция')).toBeDefined()
  })

  it('orders increases before decreases', () => {
    const { container } = render(<DeltaPreview delta={DELTA} locale="en" />)
    const directions = [...container.querySelectorAll('[data-direction]')].map((node) =>
      node.getAttribute('data-direction'),
    )
    expect(directions.slice(0, 2)).toEqual(['up', 'down'])
  })

  it('says so plainly when nothing changes', () => {
    const unchanged: Delta = { ...DELTA, changed: [], total_change: '0.00' }
    render(<DeltaPreview delta={unchanged} locale="ru" />)
    expect(screen.getByText('Цена не изменится')).toBeDefined()
  })

  it('warns when the two sides are not comparable', () => {
    const shifted: Delta = { ...DELTA, comparable: false }
    render(<DeltaPreview delta={shifted} locale="en" />)
    expect(screen.getByRole('status')).toBeDefined()
  })
})
