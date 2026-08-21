import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

import type { Locale } from '../i18n/messages'

/**
 * The last thing between a render error and a blank page.
 *
 * There was no boundary anywhere in either app. React's default on an uncaught
 * render error is to unmount the whole tree, so one bad row — a null where a
 * number was expected, a date that failed to parse — took the entire screen to
 * white. On the storefront that is a lost order; on the console it is a floor
 * that cannot see its own queue, in the middle of a shift, with no clue on
 * screen as to what happened or what to do next.
 *
 * So this does the three things a blank page does not:
 *
 * * **Says something.** In the reader's language, in the app's own chrome.
 * * **Offers the way out.** Reload is genuinely the right move for a render
 *   error — the state that produced it is gone with the page.
 * * **Reports.** `onError` gets the error and the component stack, which is what
 *   a person can quote and what a future error sink will forward.
 *
 * A class, because `componentDidCatch` has no hook equivalent — this is the one
 * React API that still requires one.
 */

const MESSAGES = {
  ru: {
    title: 'Что-то пошло не так',
    body: 'Экран не удалось отрисовать. Перезагрузка обычно помогает.',
    reload: 'Перезагрузить',
    details: 'Подробности',
  },
  en: {
    title: 'Something went wrong',
    body: 'This screen could not be drawn. Reloading usually fixes it.',
    reload: 'Reload',
    details: 'Details',
  },
} as const

export interface ErrorBoundaryProps {
  children: ReactNode
  locale?: Locale
  /** Called with whatever was thrown, plus React's component stack. */
  onError?: (error: Error, info: ErrorInfo) => void
  /** Replaces the default panel entirely, for a screen that wants its own. */
  fallback?: (error: Error, reset: () => void) => ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Logged unconditionally as well as handed to `onError`: a deployment with
    // no error sink configured must still leave the stack somewhere a person
    // can find it, and the browser console is that somewhere.
    console.error('Unhandled render error', error, info.componentStack)
    this.props.onError?.(error, info)
  }

  private reset = (): void => {
    this.setState({ error: null })
  }

  override render(): ReactNode {
    const { error } = this.state
    if (error === null) return this.props.children
    if (this.props.fallback) return this.props.fallback(error, this.reset)

    const text = MESSAGES[this.props.locale ?? 'ru']
    return (
      <div className="hv-error-boundary" role="alert">
        <h1 className="hv-error-boundary__title">{text.title}</h1>
        <p className="hv-error-boundary__body">{text.body}</p>
        <button
          type="button"
          className="hv-error-boundary__action"
          onClick={() => window.location.reload()}
        >
          {text.reload}
        </button>
        {/* The message, not the stack: a stack on screen is noise to a customer
            and the console already has it. Enough for somebody to quote. */}
        <details className="hv-error-boundary__details">
          <summary>{text.details}</summary>
          <pre>{error.message}</pre>
        </details>
      </div>
    )
  }
}
