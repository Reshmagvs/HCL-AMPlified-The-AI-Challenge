/**
 * Step 1 — describe the goal.
 *
 * This is the first screen a stranger sees, so it has three jobs before it has
 * any other: say what the product does, show what to type, and make the next
 * action obvious. Hence the one-line thesis, the three-step explainer, and three
 * real example sentences that fill the box on click.
 *
 * The profile card on the right is the most persuasive part of the product.
 * Watching goal, hours and preferences appear one by one as plain English is
 * typed is what shows that the system is reading structure rather than matching
 * keywords — so each newly-filled field animates once, and only once.
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { api, type ProfileDraft } from '../lib/api'
import { PROFILE_FIELDS, formatField, useSession } from '../lib/store'
import { Callout, DegradedBanner, ErrorPanel, Hint } from '../components/states'

const OPENER =
  'Tell me what you want to be able to do, and roughly how many hours a week you can give it.'

const EXAMPLES = [
  {
    short: 'Second-year CS student → ML engineer',
    text: 'I am a second year CS student. I already know Python and git. I want to become an ML engineer, 6 hours a week, free resources only.',
  },
  {
    short: 'Complete beginner → web developer',
    text: 'I want to learn web development from scratch, 10 hours per week, and I prefer video.',
  },
  {
    short: 'Limited data → cloud and DevOps',
    text: 'My goal is to become a cloud devops engineer. I can do 8 hrs a week and I am on limited data so text is better.',
  },
]

const HOW_IT_WORKS = [
  { n: 1, title: 'Describe your goal', body: 'In your own words. No forms, no dropdowns.' },
  { n: 2, title: 'Take a short check', body: 'A few questions so the plan starts where you are.' },
  { n: 3, title: 'Follow the order', body: 'Real resources, sequenced so nothing arrives too early.' },
]

export default function Intake() {
  const navigate = useNavigate()
  const { sessionId, transcript, profile, setSession, addTurn, setProfile, setLearner, setSeeded } =
    useSession()
  const [draft, setDraft] = useState('')
  const [ready, setReady] = useState(false)
  const [degraded, setDegraded] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [transcript.length])

  const send = useMutation({
    mutationFn: (message: string) => api.intakeMessage(message, sessionId),
    onSuccess: (data) => {
      setSession(data.session_id)
      setProfile(data.profile)
      addTurn({ role: 'assistant', text: data.assistant_message })
      setReady(data.ready)
      setDegraded(data.llm_degraded)
    },
  })

  const commit = useMutation({
    mutationFn: () => api.intakeCommit(sessionId, profile),
    onSuccess: (data) => {
      setLearner(data.learner_id)
      setSeeded(data.seeded_mastery, data.goal_names)
      navigate('/diagnostic')
    },
  })

  function submit(text: string) {
    const message = text.trim()
    if (!message || send.isPending) return
    addTurn({ role: 'learner', text: message })
    setDraft('')
    send.mutate(message)
  }

  const started = transcript.length > 0

  return (
    <div className="mx-auto max-w-[1100px]">
      {!started && <Hero />}

      <div className="grid gap-7 lg:grid-cols-[minmax(0,1fr)_330px]">
        <section className="flex min-h-[46vh] flex-col">
          {started && (
            <header className="mb-4">
              <p className="label">Step 1 of 4</p>
              <h1 className="mt-1 text-2xl">What are you aiming at?</h1>
            </header>
          )}

          <DegradedBanner show={degraded} what="replies" />

          <div className="card mt-3 flex flex-1 flex-col gap-4 overflow-y-auto p-6">
            <Bubble role="assistant" text={OPENER} />
            {transcript.map((turn, index) => (
              <Bubble key={index} role={turn.role} text={turn.text} />
            ))}
            {send.isPending && <Bubble role="assistant" text="Reading that…" pending />}
            <div ref={endRef} />
          </div>

          {send.isError && (
            <div className="mt-3">
              <ErrorPanel error={send.error} onRetry={() => send.reset()} />
            </div>
          )}

          {!started && (
            <div className="mt-4">
              <p className="label mb-2">Not sure what to write? Start from one of these</p>
              <div className="grid gap-2 sm:grid-cols-3">
                {EXAMPLES.map((example) => (
                  <button
                    key={example.short}
                    className="card p-3 text-left text-[13px] leading-snug text-ink-700 transition-shadow hover:border-clay-300 hover:shadow-lift"
                    onClick={() => submit(example.text)}
                  >
                    <span className="mb-1 block font-semibold text-ink-900">{example.short}</span>
                    <span className="text-ink-400">Click to use this</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          <form
            className="mt-4 flex gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              submit(draft)
            }}
          >
            <input
              ref={inputRef}
              className="field flex-1"
              placeholder="e.g. I want to become a data analyst, about 5 hours a week"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              aria-label="Describe your goal"
            />
            <button className="btn-primary" type="submit" disabled={send.isPending || !draft.trim()}>
              Send
            </button>
          </form>
          <Hint>
            Mention anything you already know — it is used to shorten the plan, never to skip a
            check.
          </Hint>
        </section>

        <aside className="lg:sticky lg:top-24 lg:self-start">
          <ProfileCard profile={profile} />

          <button
            className="btn-primary mt-4 w-full"
            disabled={!ready || commit.isPending}
            onClick={() => commit.mutate()}
          >
            {commit.isPending ? 'Working out your route…' : 'Build my plan →'}
          </button>

          <div className="mt-3">
            {ready ? (
              <Callout tone="accent">
                Ready. Next you will answer a few short questions so the plan starts at the right
                place.
              </Callout>
            ) : (
              <Callout>
                Two things are needed before a plan can be built: <strong>what you want to do</strong>{' '}
                and <strong>how many hours a week</strong> you have.
              </Callout>
            )}
          </div>

          {commit.isError && (
            <div className="mt-3">
              <ErrorPanel error={commit.error} onRetry={() => commit.mutate()} />
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}

function Hero() {
  return (
    <div className="mb-9">
      <div className="rule-accent mb-4" />
      <h1 className="max-w-3xl text-[34px] leading-[1.15] sm:text-[42px]">
        Learning is a dependency graph,
        <br className="hidden sm:block" /> not a search result.
      </h1>
      <p className="mt-4 max-w-2xl text-[15px] leading-relaxed text-ink-500">
        Search gives you a thousand relevant courses and no idea what to do first. Lodestar works
        out the <em>order</em> — what has to come first, what unlocks what — measures where you
        actually are, and fits the result into the hours you really have.
      </p>

      <ol className="mt-7 grid gap-3 sm:grid-cols-3">
        {HOW_IT_WORKS.map((step) => (
          <li key={step.n} className="panel flex gap-3 p-4">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-clay-500 text-[11px] font-bold text-paper-50">
              {step.n}
            </span>
            <span>
              <span className="block text-sm font-semibold text-ink-900">{step.title}</span>
              <span className="mt-0.5 block text-[13px] leading-snug text-ink-500">{step.body}</span>
            </span>
          </li>
        ))}
      </ol>
      <div className="rule mt-8" />
    </div>
  )
}

function Bubble({
  role,
  text,
  pending = false,
}: {
  role: 'learner' | 'assistant'
  text: string
  pending?: boolean
}) {
  const mine = role === 'learner'
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div
        className={[
          'max-w-[86%] animate-fade-up rounded-2xl px-4 py-2.5 text-[14px] leading-relaxed',
          mine
            ? 'rounded-br-md bg-clay-500 text-paper-50'
            : 'rounded-bl-md border border-paper-400 bg-paper-200 text-ink-700',
          pending ? 'opacity-60' : '',
        ].join(' ')}
      >
        {text}
      </div>
    </div>
  )
}

function ProfileCard({ profile }: { profile: ProfileDraft }) {
  const seen = useRef<Set<string>>(new Set())
  const filled = PROFILE_FIELDS.filter((f) => formatField(profile[f.key]) !== null).length

  return (
    <div className="card p-5">
      <div className="flex items-baseline justify-between">
        <p className="label">What we heard</p>
        <span className="font-mono text-[11px] text-ink-400">
          {filled}/{PROFILE_FIELDS.length}
        </span>
      </div>
      <p className="mt-1.5 text-[12px] leading-snug text-ink-400">
        Filled only from what you actually said. Nothing here is assumed.
      </p>

      <dl className="mt-4 space-y-1">
        {PROFILE_FIELDS.map(({ key, label }) => {
          const value = formatField(profile[key])
          const isNew = value !== null && !seen.current.has(key)
          if (value !== null) seen.current.add(key)

          return (
            <div
              key={key}
              className={`rounded-md px-2 py-1.5 ${isNew ? 'animate-field-fill' : ''}`}
            >
              <dt className="label">{label}</dt>
              <dd
                className={`mt-0.5 break-words text-[13px] ${
                  value === null ? 'text-ink-300' : 'font-medium text-ink-900'
                }`}
              >
                {value ?? '—'}
              </dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}
