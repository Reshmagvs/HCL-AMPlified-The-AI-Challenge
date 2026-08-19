/**
 * The application shell: header, health chip, and the four-step journey nav.
 *
 * The nav doubles as a progress indicator — a learner who has not committed an
 * intake cannot open the path screen, and the disabled state says why rather
 * than failing on arrival.
 */

import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { queryState } from '../lib/queryState'
import { useSession } from '../lib/store'

const STEPS = [
  { to: '/', label: 'Intake', needsLearner: false },
  { to: '/diagnostic', label: 'Diagnostic', needsLearner: true },
  { to: '/path', label: 'Path', needsLearner: true },
  { to: '/dashboard', label: 'Dashboard', needsLearner: true },
]

function HealthChip() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    // Short interval so the chip recovers on its own within seconds of the
    // backend coming back, rather than leaving a stale "offline" badge.
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  })
  const { data, failure } = queryState(health)

  if (failure) {
    return (
      <span className="chip border-signal-bad/50 text-signal-bad">
        <Dot className="bg-signal-bad" /> API offline
      </span>
    )
  }
  if (!data) {
    return (
      <span className="chip">
        <Dot className="bg-mist-500" /> checking…
      </span>
    )
  }
  const live = data.llm_provider !== 'mock' && data.llm_available
  return (
    <span className="chip" title={`${data.catalog_size} verified resources · ${data.graph_nodes} skills`}>
      <Dot className={live ? 'bg-signal-ok' : 'bg-signal-warn'} />
      {live ? 'live model' : 'no API key — mock mode'}
      <span className="hidden text-mist-500 sm:inline">
        · {data.graph_nodes} skills · {data.catalog_size} resources
      </span>
    </span>
  )
}

function Dot({ className }: { className: string }) {
  return <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${className}`} />
}

export default function Layout({ children }: { children: ReactNode }) {
  const learnerId = useSession((s) => s.learnerId)
  const reset = useSession((s) => s.reset)

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-20 border-b border-ink-700/70 bg-ink-950/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-2.5">
            <Compass />
            <span className="text-lg font-bold tracking-tight">Lodestar</span>
          </div>

          <nav className="order-3 -mx-1 flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto">
            {STEPS.map((step) => {
              const blocked = step.needsLearner && learnerId === null
              return (
                <NavLink
                  key={step.to}
                  to={blocked ? '/' : step.to}
                  aria-disabled={blocked}
                  title={blocked ? 'Finish intake first' : undefined}
                  className={({ isActive }) =>
                    [
                      'whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
                      blocked
                        ? 'cursor-not-allowed text-mist-500/50'
                        : isActive
                          ? 'bg-ink-800 text-mist-100'
                          : 'text-mist-500 hover:text-mist-100',
                    ].join(' ')
                  }
                >
                  {step.label}
                </NavLink>
              )
            })}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <HealthChip />
            {learnerId !== null && (
              <button
                className="text-xs font-medium text-mist-500 hover:text-mist-100"
                onClick={() => {
                  reset()
                  window.location.assign('/')
                }}
              >
                Start over
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-6 sm:px-6">{children}</main>

      <footer className="mx-auto w-full max-w-[1500px] px-4 pb-6 sm:px-6">
        <div className="edge-rule mb-3" />
        <p className="text-xs text-mist-500">
          Learning is a dependency graph, not a search result. Every resource here is a real,
          HTTP-verified link from a curated catalog.
        </p>
      </footer>
    </div>
  )
}

/** The node-and-edge motif, used as the product mark. */
function Compass() {
  return (
    <svg aria-hidden width="26" height="26" viewBox="0 0 26 26" fill="none">
      <circle cx="6" cy="19" r="3" fill="#f59331" />
      <circle cx="13" cy="8" r="3" className="fill-mist-300" />
      <circle cx="21" cy="17" r="2.5" className="fill-mist-500" />
      <path d="M7.6 16.6 11.4 10.6M15 10 19.4 15" stroke="#7784a6" strokeWidth="1.4" />
    </svg>
  )
}
