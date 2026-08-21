/**
 * Shared states and guidance: loading, empty, failed, degraded, and hints.
 *
 * They live in one file because they must look and behave identically on every
 * screen. An app that invents a new error style per page reads as unfinished,
 * and a page that silently renders nothing when the backend is down is worse
 * than one that says so and offers a way forward.
 *
 * `Hint` and `Callout` are here for the same reason: guidance should look like
 * part of the product, not like a sticker added afterwards.
 */

import type { ReactNode } from 'react'
import { ApiError } from '../lib/api'

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded-lg bg-paper-300 ${className}`} />
}

export function LoadingPanel({ label = 'Loading', rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="card space-y-3 p-6" role="status" aria-live="polite">
      <p className="label">{label}…</p>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={`h-3.5 ${['w-2/3', 'w-full', 'w-1/2'][i % 3]}`} />
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
    <div className="card border-rust-500/35 bg-rust-100/50 p-6" role="alert">
      <h2 className="text-base font-semibold text-rust-700">
        {offline ? 'Cannot reach Lodestar' : title}
      </h2>
      <p className="mt-1.5 break-words text-sm text-ink-500">{message}</p>
      {offline && (
        <p className="mt-2 text-sm text-ink-500">
          The app server is not responding. Start it with{' '}
          <code className="rounded bg-paper-200 px-1.5 py-0.5 font-mono text-[12px]">run.bat</code>{' '}
          and try again — nothing you have done is lost.
        </p>
      )}
      {onRetry && (
        <button className="btn-secondary mt-4" onClick={onRetry}>
          Try again
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
    <div className="card flex flex-col items-start gap-3 p-10">
      <svg aria-hidden width="52" height="24" viewBox="0 0 52 24" fill="none" className="opacity-45">
        <path d="M8 12h14M30 12h14" stroke="#D5C9B4" strokeWidth="1.5" strokeDasharray="3 3" />
        <circle cx="4" cy="12" r="3.5" fill="#B0563A" opacity=".5" />
        <circle cx="26" cy="12" r="3.5" fill="#D5C9B4" />
        <circle cx="48" cy="12" r="3.5" fill="#D5C9B4" />
      </svg>
      <h2 className="text-lg font-semibold">{title}</h2>
      {children && <div className="max-w-prose text-sm leading-relaxed text-ink-500">{children}</div>}
      {action}
    </div>
  )
}

/** A short piece of inline guidance, with a lightbulb-ish marker. */
export function Hint({ children }: { children: ReactNode }) {
  return (
    <p className="hint">
      <span aria-hidden className="mt-[3px] text-clay-500">
        ◆
      </span>
      <span>{children}</span>
    </p>
  )
}

/** A boxed aside for guidance that deserves more weight than a Hint. */
export function Callout({
  tone = 'neutral',
  title,
  children,
}: {
  tone?: 'neutral' | 'accent' | 'warn'
  title?: string
  children: ReactNode
}) {
  const tones = {
    neutral: 'border-paper-400 bg-paper-200/60 text-ink-500',
    accent: 'border-clay-300/60 bg-clay-50 text-clay-700',
    warn: 'border-amber-500/35 bg-amber-100/70 text-amber-700',
  }
  return (
    <div className={`rounded-xl border px-4 py-3 text-[13px] leading-relaxed ${tones[tone]}`}>
      {title && <p className="mb-0.5 font-semibold">{title}</p>}
      {children}
    </div>
  )
}

/**
 * Shown when a response came back with `llm_degraded`.
 *
 * Deliberately reassuring rather than alarming: the ordering, the schedule and
 * the resources are all computed without a model, so the only thing affected is
 * how the sentences read. Saying that plainly stops the banner from looking like
 * a failure the learner has to act on.
 *
 * It also does not claim the model is *unavailable*, because usually it is not:
 * on a laptop CPU the writing layer is skipped because it would take longer
 * than the wait is worth. Saying "unavailable" would be a guess about a cause,
 * and a wrong one most of the time.
 */
export function DegradedBanner({ show, what = 'wording' }: { show: boolean; what?: string }) {
  if (!show) return null
  return (
    <Callout tone="warn" title={`Plain ${what} for now`}>
      These sentences come from a template rather than the writing assistant. Your ordering,
      schedule and resources are calculated the same way either way — nothing below is affected.
    </Callout>
  )
}
