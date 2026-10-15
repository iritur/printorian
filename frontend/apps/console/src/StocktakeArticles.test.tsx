/**
 * The stocktake's article lines (#35): counted in their own unit, a dash until
 * counted, and the count sent to the article's own route.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { ArticleLine } from './StocktakeArticles'
import { ArticleCountTable } from './StocktakeArticles'

const line: ArticleLine = {
  article_id: 'a1',
  code: 'box-a',
  name: 'Коробка A',
  unit: 'piece',
  cell_address: 'C6-1',
  expected: '40.00',
  counted: null,
  counted_at: null,
  variance: null,
}

describe('the article count table', () => {
  it('shows the book in its unit and a dash until somebody counts', () => {
    render(<ArticleCountTable lines={[line]} locale="ru" mayManage={false} onCount={vi.fn()} />)

    const row = screen.getByText('box-a').closest('tr')
    expect(row?.textContent).toMatch(/40 шт/)
    expect(row?.textContent).toMatch(/—/)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('sends what was found for that item', async () => {
    const onCount = vi.fn(() => Promise.resolve())
    render(<ArticleCountTable lines={[line]} locale="ru" mayManage={true} onCount={onCount} />)

    fireEvent.change(screen.getByLabelText('box-a Коробка A'), { target: { value: '38' } })
    fireEvent.click(screen.getByRole('button', { name: '✓' }))

    await waitFor(() => expect(onCount).toHaveBeenCalledWith('a1', '38'))
  })

  it('draws nothing at all when no item was lined up', () => {
    const { container } = render(
      <ArticleCountTable lines={[]} locale="ru" mayManage={true} onCount={vi.fn()} />,
    )
    expect(container.innerHTML).toBe('')
  })
})
