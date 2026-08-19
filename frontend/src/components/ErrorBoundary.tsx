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
        <div className="card border-rust-500/35 bg-rust-100/50 p-7" role="alert">
          <h2 className="text-base font-semibold text-rust-700">This screen could not be drawn</h2>
          <p className="mt-1.5 text-sm text-ink-500">{error.message}</p>
          <p className="mt-3 text-sm text-ink-500">
            Usually this means the app server stopped while you were using it. Nothing you have done
            is lost — check it is running, then reload.
          </p>
          <div className="mt-4 flex gap-3">
            <button className="btn-primary" onClick={() => this.setState({ error: null })}>
              Try again
            </button>
            <button className="btn-secondary" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </div>
      </div>
    )
  }
}
