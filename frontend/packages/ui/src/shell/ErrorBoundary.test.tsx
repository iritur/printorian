import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ErrorBoundary } from './ErrorBoundary'

function Explode({ when }: { when: boolean }): React.ReactElement {
  if (when) throw new Error('the row had no material')
  return <p>the screen</p>
}

describe('ErrorBoundary', () => {
  beforeEach(() => {
    // React logs the caught error itself, and the boundary logs it again on
    // purpose. Silenced so a passing test does not print two stacks.
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders its children when nothing throws', () => {
    render(
      <ErrorBoundary>
        <Explode when={false} />
      </ErrorBoundary>,
    )
    expect(screen.getByText('the screen')).toBeInTheDocument()
  })

  it('shows a way out instead of a blank page when a child throws', () => {
    render(
      <ErrorBoundary>
        <Explode when={true} />
      </ErrorBoundary>,
    )
    // The point of the whole component: something is on screen, and it is not
    // the tree that failed.
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Перезагрузить' })).toBeInTheDocument()
    expect(screen.queryByText('the screen')).not.toBeInTheDocument()
  })

  it('reports what was thrown, with the component stack', () => {
    const onError = vi.fn()
    render(
      <ErrorBoundary onError={onError}>
        <Explode when={true} />
      </ErrorBoundary>,
    )
    expect(onError).toHaveBeenCalled()
    const call = onError.mock.calls[0] ?? []
    expect((call[0] as Error).message).toBe('the row had no material')
    expect(call[1]).toHaveProperty('componentStack')
  })

  it('speaks the reader’s language', () => {
    render(
      <ErrorBoundary locale="en">
        <Explode when={true} />
      </ErrorBoundary>,
    )
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
  })

  it('lets a caller replace the panel entirely', () => {
    render(
      <ErrorBoundary fallback={(error) => <p>caught: {error.message}</p>}>
        <Explode when={true} />
      </ErrorBoundary>,
    )
    expect(screen.getByText('caught: the row had no material')).toBeInTheDocument()
  })
})
