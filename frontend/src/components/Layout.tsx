/**
 * The application shell: wordmark, a four-step progress rail, and system status.
 *
 * The rail is the main piece of wayfinding in the product. It is not only
 * navigation — it tells a first-time visitor how many steps there are, which one
 * they are on and which are finished, before they have read anything. A step
 * that is not yet reachable says why on hover rather than failing on arrival.
 */

import type { ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { queryState } from '../lib/queryState'
import { useSession } from '../lib/store'
import { UsagePanel } from './UsagePanel'

const STEPS = [
  { to: '/', label: 'Describe', hint: 'Tell us your goal' },
  { to: '/diagnostic', label: 'Measure', hint: 'A short placement check' },
  { to: '/path', label: 'Follow', hint: 'Your ordered plan' },
  { to: '/dashboard', label: 'Track', hint: 'Progress and next actions' },
]

export default function Layout({ children }: { children: ReactNode }) {
  const learnerId = useSession((s) => s.learnerId)
  const started = useSession((s) => s.transcript.length > 0)
  const reset = useSession((s) => s.reset)
  const { pathname } = useLocation()
  const currentStep = Math.max(0, STEPS.findIndex((step) => step.to === pathname))

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-20 border-b border-paper-400 bg-paper-100/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-8 gap-y-3 px-4 py-3 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2.5" aria-label="Lodestar home">
            <Mark />
            <span className="font-serif text-[19px] font-semibold text-ink-900">Lodestar</span>
          </NavLink>

          <nav aria-label="Progress" className="order-3 w-full sm:order-none sm:w-auto">
            <ol className="flex items-center gap-1 overflow-x-auto">
              {STEPS.map((step, index) => {
                const locked = index > 0 && learnerId === null
                const done = learnerId !== null && index < currentStep
                return (
                  <li key={step.to} className="flex items-center">
                    {index > 0 && (
                      <span aria-hidden className="mx-1 h-px w-4 shrink-0 bg-paper-500 sm:w-6" />
                    )}
                    <NavLink
                      to={locked ? '/' : step.to}
                      title={locked ? 'Describe your goal first' : step.hint}
                      aria-disabled={locked}
                      className={({ isActive }) =>
                        [
                          'flex items-center gap-2 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-sm transition-colors',
                          locked
                            ? 'cursor-not-allowed text-ink-300'
                            : isActive
                              ? 'bg-clay-50 font-semibold text-clay-700'
                              : 'font-medium text-ink-500 hover:bg-paper-200 hover:text-ink-900',
                        ].join(' ')
                      }
                    >
                      <StepDot index={index} active={index === currentStep} done={done} locked={locked} />
                      {step.label}
                    </NavLink>
                  </li>
                )
              })}
            </ol>
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <UsagePanel />
            <StatusChip />
            {/*
              Offered from the first typed message, not only once a plan
              exists. Changing your mind halfway through describing a goal is
              the most likely moment to want to start again, and until this it
              was the one moment with no way to.
            */}
            {(learnerId !== null || started) && (
              <button
                className="btn-quiet text-xs"
                title={
                  learnerId !== null
                    ? 'Clear this learner and start a new plan'
                    : 'Clear what you have typed and start the conversation again'
                }
                onClick={() => {
                  if (
                    learnerId !== null &&
                    !window.confirm('Start over? This clears your plan and progress.')
                  ) {
                    return
                  }
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

      <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-7 sm:px-6">{children}</main>

      <footer className="mx-auto w-full max-w-[1400px] px-4 pb-7 sm:px-6">
        <div className="rule mb-3" />
        <p className="text-xs leading-relaxed text-ink-400">
          Every resource in Lodestar is a real, working link from a checked catalogue — nothing is
          invented. Your plan is stored only on this machine.
        </p>
      </footer>
    </div>
  )
}

function StepDot({
  index,
  active,
  done,
  locked,
}: {
  index: number
  active: boolean
  done: boolean
  locked: boolean
}) {
  const base = 'flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold'
  if (done) {
    return (
      <span aria-hidden className={`${base} bg-sage-500 text-paper-50`}>
        ✓
      </span>
    )
  }
  return (
    <span
      aria-hidden
      className={[
        base,
        active
          ? 'bg-clay-500 text-paper-50'
          : locked
            ? 'border border-paper-500 text-ink-300'
            : 'border border-paper-500 text-ink-400',
      ].join(' ')}
    >
      {index + 1}
    </span>
  )
}

/**
 * System status phrased for a person rather than an operator: what the app can
 * do right now, not which subsystem is up.
 */
function StatusChip() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  })
  const { data, failure } = queryState(health)

  if (failure) {
    return (
      <span className="chip border-rust-500/40 bg-rust-100 text-rust-700">
        <Dot className="bg-rust-500" /> Offline
      </span>
    )
  }
  if (!data) {
    return (
      <span className="chip">
        <Dot className="bg-ink-300" /> Checking
      </span>
    )
  }

  const detail =
    `${data.graph_nodes} skills · ${data.catalog_size} checked resources · ` +
    `${data.question_bank} questions · search: ${data.embedder} · text: ${data.llm_provider}`
  return (
    <span className="chip" title={detail}>
      <Dot className="bg-sage-500" />
      <span className="hidden sm:inline">{data.catalog_size} resources</span>
      <span className="sm:hidden">Ready</span>
    </span>
  )
}

function Dot({ className }: { className: string }) {
  return <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${className}`} />
}

/** Three points and two edges: the dependency idea, drawn small. */
function Mark() {
  return (
    <svg aria-hidden width="24" height="24" viewBox="0 0 24 24" fill="none">
      <path d="M6 17.5 11.5 7.5M13 7.5 18.5 15" stroke="#B0563A" strokeWidth="1.3" opacity=".55" />
      <circle cx="5.5" cy="18" r="2.6" fill="#B0563A" />
      <circle cx="12.2" cy="6.4" r="2.6" fill="#2A251F" opacity=".72" />
      <circle cx="19" cy="15.6" r="2.2" fill="#2A251F" opacity=".32" />
    </svg>
  )
}
