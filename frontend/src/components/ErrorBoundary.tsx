/**
 * A last line of defence against the white screen.
 *
 * Every screen already handles loading, empty and error states explicitly. This
 * exists for the case none of them cover: an unexpected render-time exception,
 * which React responds to by unmounting the entire tree. A blank page is the
 * worst possible failure mode for a demo, so anything that escapes lands here
 * with a message and a way out.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Lodestar render error:', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="mx-auto max-w-2xl p-6">
        <div className="card border-signal-bad/40 p-6" role="alert">
          <p className="font-semibold text-signal-bad">This screen could not be rendered</p>
          <p className="mt-1 text-sm text-mist-500">{error.message}</p>
          <p className="mt-3 text-sm text-mist-500">
            The most common cause is the API being unreachable mid-session. Check that the backend
            is running, then reload.
          </p>
          <div className="mt-4 flex gap-3">
            <button className="btn-primary" onClick={() => this.setState({ error: null })}>
              Try again
            </button>
            <button className="btn-ghost" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </div>
      </div>
    )
  }
}
