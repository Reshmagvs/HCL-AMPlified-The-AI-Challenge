/**
 * Intake: conversation on the left, structure emerging on the right.
 *
 * The profile card is the point of this screen. Watching `goal_text`,
 * `hours_per_week` and `cost_pref` appear one by one as the learner types plain
 * English is the clearest possible demonstration that the system is extracting
 * structure rather than keyword-matching — so each newly-filled field animates
 * once, and only once.
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { api, type ProfileDraft } from '../lib/api'
import { PROFILE_FIELDS, formatField, useSession } from '../lib/store'
import { DegradedBanner, ErrorPanel } from '../components/states'

const OPENER =
  'Tell me what you want to be able to do, and roughly how many hours a week you can give it.'

const EXAMPLES = [
  'I am a 2nd year CS student. I know Python and git. I want to become an ML engineer, 6 hours a week, free resources only.',
  'I want to learn web development from scratch, 10 hours per week, I prefer video.',
  'My goal is to become a cloud devops engineer. I can do 8 hrs a week and I am on limited data.',
]

export default function Intake() {
  const navigate = useNavigate()
  const { sessionId, transcript, profile, setSession, addTurn, setProfile, setLearner } =
    useSession()
  const [draft, setDraft] = useState('')
  const [ready, setReady] = useState(false)
  const [degraded, setDegraded] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

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

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
      <section className="flex min-h-[60vh] flex-col">
        <header className="mb-4">
          <p className="label">Step 1 of 4</p>
          <h1 className="text-2xl font-bold tracking-tight">What are you aiming at?</h1>
        </header>

        <DegradedBanner show={degraded} what="conversation" />

        <div className="card mt-3 flex flex-1 flex-col gap-4 overflow-y-auto p-5">
          <Bubble role="assistant" text={OPENER} />
          {transcript.map((turn, index) => (
            <Bubble key={index} role={turn.role} text={turn.text} />
          ))}
          {send.isPending && <Bubble role="assistant" text="…" pending />}
          <div ref={endRef} />
        </div>

        {send.isError && (
          <div className="mt-3">
            <ErrorPanel error={send.error} onRetry={() => send.reset()} />
          </div>
        )}

        {transcript.length === 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                className="chip max-w-full text-left hover:border-ember-500/60 hover:text-mist-100"
                onClick={() => submit(example)}
              >
                <span className="truncate">{example.slice(0, 58)}…</span>
              </button>
            ))}
          </div>
        )}

        <form
          className="mt-3 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            submit(draft)
          }}
        >
          <input
            className="min-w-0 flex-1 rounded-lg border border-ink-600 bg-ink-900 px-4 py-2.5 text-sm placeholder:text-mist-500/70"
            placeholder="Describe your goal in your own words…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            aria-label="Message"
          />
          <button className="btn-primary" type="submit" disabled={send.isPending || !draft.trim()}>
            Send
          </button>
        </form>
      </section>

      <aside className="lg:sticky lg:top-24 lg:self-start">
        <ProfileCard profile={profile} />
        <button
          className="btn-primary mt-4 w-full"
          disabled={!ready || commit.isPending}
          onClick={() => commit.mutate()}
        >
          {commit.isPending ? 'Resolving your goal…' : 'Build my path'}
        </button>
        {!ready && (
          <p className="mt-2 text-xs text-mist-500">
            A goal and an hours-per-week figure are both needed before a path can be sequenced.
          </p>
        )}
        {commit.isError && (
          <div className="mt-3">
            <ErrorPanel error={commit.error} onRetry={() => commit.mutate()} />
          </div>
        )}
      </aside>
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
          'max-w-[85%] animate-fade-up rounded-2xl px-4 py-2.5 text-sm leading-relaxed',
          mine ? 'bg-ember-500 text-ink-950' : 'bg-ink-800 text-mist-100',
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

  return (
    <div className="card p-5">
      <p className="label">Extracted profile</p>
      <p className="mt-1 text-xs text-mist-500">
        Filled only from what you actually said. Nothing here is guessed.
      </p>
      <dl className="mt-4 space-y-2.5">
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
                className={`mt-0.5 break-words text-sm ${
                  value === null ? 'text-mist-500/50' : 'text-mist-100'
                }`}
              >
                {value ?? 'not yet mentioned'}
              </dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}
