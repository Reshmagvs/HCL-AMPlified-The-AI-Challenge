/**
 * The three states every screen needs: loading, empty, and failed-with-retry.
 *
 * They live in one file because they must look and behave identically
 * everywhere — an app that invents a new error style per screen reads as
 * unfinished, and a screen that silently renders nothing when the backend is
 * down is worse than one that says so.
 */

import type { ReactNode } from 'react'
import { ApiError } from '../lib/api'

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded-lg bg-ink-800/70 ${className}`} />
}

export function LoadingPanel({ label = 'Loading', rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="card space-y-3 p-5" role="status" aria-live="polite">
      <p className="label">{label}…</p>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={`h-4 ${i % 3 === 0 ? 'w-2/3' : i % 3 === 1 ? 'w-full' : 'w-1/2'}`} />
      ))}
    </div>
  )
}

export function ErrorPanel({
  error,
  onRetry,
  title = 'Something went wrong',
}: {
  error: unknown
  onRetry?: () => void
  title?: string
}) {
  const offline = error instanceof ApiError && error.isOffline
  const message = error instanceof Error ? error.message : String(error)

  return (
    <div className="card border-signal-bad/40 p-5" role="alert">
      <p className="font-semibold text-signal-bad">{offline ? 'Backend unreachable' : title}</p>
      <p className="mt-1 break-words text-sm text-mist-500">{message}</p>
      {offline && (
        <p className="mt-2 text-sm text-mist-500">
          Start it with <code className="font-mono text-mist-300">run.bat --backend</code>, then retry.
        </p>
      )}
      {onRetry && (
        <button className="btn-ghost mt-4" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  )
}

export function EmptyPanel({
  title,
  children,
  action,
}: {
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="card flex flex-col items-start gap-3 p-8">
      <div aria-hidden className="flex items-center gap-2 opacity-60">
        <span className="h-2.5 w-2.5 rounded-full bg-ember-500" />
        <span className="h-px w-8 bg-mist-500" />
        <span className="h-2.5 w-2.5 rounded-full border border-mist-500" />
        <span className="h-px w-8 bg-mist-500" />
        <span className="h-2.5 w-2.5 rounded-full border border-mist-500" />
      </div>
      <h2 className="text-lg font-semibold">{title}</h2>
      {children && <div className="text-sm text-mist-500">{children}</div>}
      {action}
    </div>
  )
}

export function DegradedBanner({ show, what = 'wording' }: { show: boolean; what?: string }) {
  if (!show) return null
  return (
    <div className="rounded-lg border border-signal-warn/40 bg-signal-warn/10 px-4 py-2.5 text-sm text-signal-warn">
      <strong className="font-semibold">Some {what} fell back to a template.</strong>{' '}
      The language model was unavailable or rate-limited. Every recommendation, ordering and
      schedule below is computed deterministically and is unaffected.
    </div>
  )
}
