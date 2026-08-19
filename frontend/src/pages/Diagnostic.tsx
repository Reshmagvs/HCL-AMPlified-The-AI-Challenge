/**
 * Diagnostic: one question at a time, with the confidence meter visible.
 *
 * Showing confidence rising is what makes the adaptivity legible — the learner
 * can see that six questions were enough because each one was chosen to resolve
 * the most uncertainty, not because the quiz happened to be short.
 *
 * "I don't know" is styled as a real option, not a giveaway. Guessing pollutes
 * the measurement, so the interface must not make abstaining feel like failure.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { queryState } from '../lib/queryState'
import { useSession } from '../lib/store'
import { DegradedBanner, ErrorPanel, LoadingPanel } from '../components/states'

export default function Diagnostic() {
  const learnerId = useSession((s) => s.learnerId)!
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<number | null>(null)
  const [feedback, setFeedback] = useState<{ correct: boolean; skill: string } | null>(null)

  const question = useQuery({
    queryKey: ['diagnostic', learnerId],
    queryFn: () => api.nextQuestion(learnerId),
  })

  const answer = useMutation({
    mutationFn: (input: { index: number | null; dontKnow: boolean }) =>
      api.answerQuestion(question.data!.quiz_item_id!, input.index, input.dontKnow),
    onSuccess: (result) => {
      setFeedback({ correct: result.correct, skill: result.skill_id })
      setSelected(null)
      setTimeout(() => {
        setFeedback(null)
        void queryClient.invalidateQueries({ queryKey: ['diagnostic', learnerId] })
      }, 900)
    },
  })

  const generate = useMutation({
    mutationFn: () => api.generatePath(learnerId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['path', learnerId] })
      navigate('/path')
    },
  })

  const state = queryState(question)
  if (!state.data) {
    return state.failure ? (
      <ErrorPanel error={state.failure} onRetry={state.retry} />
    ) : (
      <LoadingPanel label="Choosing the most informative question" rows={4} />
    )
  }

  const data = state.data
  const progress = Math.min(1, data.asked / data.max_questions)

  if (data.done) {
    return (
      <div className="mx-auto max-w-2xl">
        <header className="mb-4">
          <p className="label">Step 2 of 4 — complete</p>
          <h1 className="text-2xl font-bold tracking-tight">We know enough to place you.</h1>
        </header>
        <div className="card space-y-4 p-6">
          <div className="flex flex-wrap items-center gap-6">
            <Ring value={data.confidence} label="confidence" />
            <div className="text-sm text-mist-500">
              <p>
                <span className="font-mono text-mist-100">{data.asked}</span> question
                {data.asked === 1 ? '' : 's'} asked, chosen by how much uncertainty each one
                resolved.
              </p>
              <p className="mt-1">
                Your measured level replaces guesswork: skills you demonstrated are dropped from the
                path entirely.
              </p>
            </div>
          </div>
          <button
            className="btn-primary w-full"
            onClick={() => generate.mutate()}
            disabled={generate.isPending}
          >
            {generate.isPending ? 'Sequencing your path…' : 'Generate my learning path'}
          </button>
          {generate.isError && <ErrorPanel error={generate.error} onRetry={() => generate.mutate()} />}
        </div>
      </div>
    )
  }

  const locked = answer.isPending || feedback !== null

  return (
    <div className="mx-auto max-w-2xl">
      <header className="mb-4">
        <p className="label">Step 2 of 4</p>
        <h1 className="text-2xl font-bold tracking-tight">Let us measure, not assume.</h1>
      </header>

      <DegradedBanner show={data.llm_degraded} what="question wording" />

      <div className="card mt-3 p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <Ring value={progress} label={`${data.asked}/${data.max_questions}`} />
            <div>
              <p className="label">Measuring</p>
              <p className="text-sm font-semibold">{data.skill_name}</p>
            </div>
          </div>
          <div className="min-w-[140px]">
            <p className="label">Confidence</p>
            <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-ink-700">
              <div
                className="h-full rounded-full bg-ember-500 transition-[width] duration-500"
                style={{ width: `${Math.round(data.confidence * 100)}%` }}
              />
            </div>
            <p className="mt-1 font-mono text-xs text-mist-500">
              {Math.round(data.confidence * 100)}%
            </p>
          </div>
        </div>

        <div className="edge-rule my-5" />

        <p className="text-base font-medium leading-relaxed">{data.question}</p>

        <div className="mt-5 space-y-2">
          {data.options.map((option, index) => (
            <button
              key={index}
              disabled={locked}
              onClick={() => setSelected(index)}
              className={[
                'w-full rounded-lg border px-4 py-3 text-left text-sm transition-colors',
                selected === index
                  ? 'border-ember-500 bg-ember-500/10 text-mist-100'
                  : 'border-ink-600 hover:border-ink-500',
                locked ? 'opacity-60' : '',
              ].join(' ')}
            >
              <span className="mr-2 font-mono text-xs text-mist-500">
                {String.fromCharCode(65 + index)}
              </span>
              {option}
            </button>
          ))}
        </div>

        {feedback && (
          <p
            className={`mt-4 text-sm font-medium ${
              feedback.correct ? 'text-signal-ok' : 'text-signal-warn'
            }`}
            role="status"
          >
            {feedback.correct
              ? 'Correct — that skill leaves your path, and its prerequisites gain weak evidence too.'
              : 'Noted. That skill stays in your path, placed where it belongs.'}
          </p>
        )}

        <div className="mt-5 flex flex-wrap gap-3">
          <button
            className="btn-primary"
            disabled={selected === null || locked}
            onClick={() => answer.mutate({ index: selected, dontKnow: false })}
          >
            Submit answer
          </button>
          <button
            className="btn-ghost"
            disabled={locked}
            onClick={() => answer.mutate({ index: null, dontKnow: true })}
            title="A clean signal, not a failure. Guessing would pollute the measurement."
          >
            I don&apos;t know
          </button>
        </div>

        {answer.isError && (
          <div className="mt-4">
            <ErrorPanel error={answer.error} onRetry={() => answer.reset()} />
          </div>
        )}
      </div>
    </div>
  )
}

export function Ring({ value, label }: { value: number; label: string }) {
  const radius = 26
  const circumference = 2 * Math.PI * radius
  const filled = Math.max(0, Math.min(1, value))

  return (
    <div className="relative h-[68px] w-[68px] shrink-0">
      <svg viewBox="0 0 68 68" className="h-full w-full -rotate-90">
        <circle cx="34" cy="34" r={radius} className="fill-none stroke-ink-700" strokeWidth="6" />
        <circle
          cx="34"
          cy="34"
          r={radius}
          className="fill-none stroke-ember-500 transition-[stroke-dashoffset] duration-500"
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - filled)}
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center font-mono text-[11px] text-mist-300">
        {label}
      </span>
    </div>
  )
}
