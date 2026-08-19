/**
 * Step 2 — the placement check.
 *
 * Two things have to be legible here or the screen feels like an obstacle:
 * *why* there is a quiz at all, and *when it will end*. So the intro explains
 * the trade in one line, the ring shows questions asked against the maximum, and
 * the confidence bar shows the thing that actually decides when to stop.
 *
 * "I don't know" is styled as a genuine option, not a giveaway. Guessing
 * pollutes the measurement, so the interface must not make abstaining feel like
 * failure.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { queryState } from '../lib/queryState'
import { useSession } from '../lib/store'
import { Callout, DegradedBanner, ErrorPanel, Hint, LoadingPanel } from '../components/states'

export default function Diagnostic() {
  const learnerId = useSession((s) => s.learnerId)!
  const seededSkills = useSession((s) => s.seededSkills)
  const goalNames = useSession((s) => s.goalNames)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<number | null>(null)
  const [feedback, setFeedback] = useState<{ correct: boolean } | null>(null)

  const question = useQuery({
    queryKey: ['diagnostic', learnerId],
    queryFn: () => api.nextQuestion(learnerId),
  })

  const answer = useMutation({
    mutationFn: (input: { index: number | null; dontKnow: boolean }) =>
      api.answerQuestion(question.data!.quiz_item_id!, input.index, input.dontKnow),
    onSuccess: (result) => {
      setFeedback({ correct: result.correct })
      setSelected(null)
      setTimeout(() => {
        setFeedback(null)
        void queryClient.invalidateQueries({ queryKey: ['diagnostic', learnerId] })
      }, 850)
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
      <LoadingPanel label="Choosing the most useful question" rows={4} />
    )
  }

  const data = state.data
  const seeded = Object.keys(seededSkills)

  if (data.done) {
    return (
      <div className="mx-auto max-w-2xl">
        <header className="mb-5">
          <p className="label">Step 2 of 4 — done</p>
          <h1 className="mt-1 text-2xl">We know enough to place you.</h1>
        </header>

        <div className="card space-y-5 p-7">
          <div className="flex flex-wrap items-center gap-6">
            <Ring value={data.confidence} caption={`${Math.round(data.confidence * 100)}%`} />
            <div className="max-w-sm text-sm leading-relaxed text-ink-500">
              <p>
                <strong className="font-semibold text-ink-900">{data.asked}</strong>{' '}
                question{data.asked === 1 ? '' : 's'} was enough. Each one was chosen to settle the
                most uncertainty, so a few go a long way.
              </p>
              <p className="mt-2">
                Anything you demonstrated is dropped from the plan entirely — you will not be sent
                to relearn it.
              </p>
            </div>
          </div>

          <button
            className="btn-primary w-full"
            onClick={() => generate.mutate()}
            disabled={generate.isPending}
          >
            {generate.isPending ? 'Working out the order…' : 'Show me my plan →'}
          </button>

          {generate.isError && <ErrorPanel error={generate.error} onRetry={() => generate.mutate()} />}
        </div>
      </div>
    )
  }

  const locked = answer.isPending || feedback !== null
  const progress = Math.min(1, data.asked / Math.max(1, data.max_questions))

  return (
    <div className="mx-auto max-w-2xl">
      <header className="mb-5">
        <p className="label">Step 2 of 4</p>
        <h1 className="mt-1 text-2xl">A few questions, so we can start in the right place.</h1>
        <p className="mt-2 max-w-xl text-sm leading-relaxed text-ink-500">
          Answering honestly makes your plan shorter, not longer: everything you show you already
          know is removed from it.
        </p>
      </header>

      {data.asked === 0 && seeded.length > 0 && (
        <div className="mb-4">
          <Callout tone="accent" title="Noted from what you told us">
            {goalNames.length > 0 && <>Aiming at {goalNames.join(' and ')}. </>}
            We recorded {seeded.length} skill{seeded.length === 1 ? '' : 's'} you mentioned as a
            starting estimate. That never removes anything from your plan on its own — these
            questions are what confirm it.
          </Callout>
        </div>
      )}

      <DegradedBanner show={data.llm_degraded} what="question wording" />

      <div className="card mt-3 p-7">
        <div className="flex flex-wrap items-center justify-between gap-5">
          <div className="flex items-center gap-4">
            <Ring value={progress} caption={`${data.asked}/${data.max_questions}`} />
            <div>
              <p className="label">Checking</p>
              <p className="text-[15px] font-semibold text-ink-900">{data.skill_name}</p>
            </div>
          </div>
          <div className="min-w-[150px]">
            <div className="flex items-baseline justify-between">
              <p className="label">Confidence</p>
              <span className="font-mono text-[11px] text-ink-400">
                {Math.round(data.confidence * 100)}%
              </span>
            </div>
            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-paper-300">
              <div
                className="h-full rounded-full bg-clay-500 transition-[width] duration-500"
                style={{ width: `${Math.max(3, Math.round(data.confidence * 100))}%` }}
              />
            </div>
            <p className="mt-1 text-[11px] text-ink-400">Stops early once this is high enough</p>
          </div>
        </div>

        <div className="rule my-6" />

        <p className="text-[16px] font-medium leading-relaxed text-ink-900">{data.question}</p>

        <div className="mt-5 space-y-2">
          {data.options.map((option, index) => (
            <button
              key={index}
              disabled={locked}
              onClick={() => setSelected(index)}
              className={[
                'flex w-full items-start gap-3 rounded-lg border px-4 py-3 text-left text-sm transition-colors',
                selected === index
                  ? 'border-clay-500 bg-clay-50 text-ink-900'
                  : 'border-paper-400 bg-paper-50 hover:border-paper-500 hover:bg-paper-200',
                locked ? 'opacity-60' : '',
              ].join(' ')}
            >
              <span
                className={[
                  'mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold',
                  selected === index
                    ? 'border-clay-500 bg-clay-500 text-paper-50'
                    : 'border-paper-500 text-ink-400',
                ].join(' ')}
              >
                {String.fromCharCode(65 + index)}
              </span>
              <span>{option}</span>
            </button>
          ))}
        </div>

        {feedback && (
          <p
            className={`mt-4 text-sm font-medium ${
              feedback.correct ? 'text-sage-700' : 'text-amber-700'
            }`}
            role="status"
          >
            {feedback.correct
              ? 'Correct — that one leaves your plan, and its groundwork gets partial credit too.'
              : 'Noted. That one stays in your plan, placed where it belongs.'}
          </p>
        )}

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button
            className="btn-primary"
            disabled={selected === null || locked}
            onClick={() => answer.mutate({ index: selected, dontKnow: false })}
          >
            Submit answer
          </button>
          <button
            className="btn-secondary"
            disabled={locked}
            onClick={() => answer.mutate({ index: null, dontKnow: true })}
          >
            I don&apos;t know
          </button>
        </div>

        <div className="mt-3">
          <Hint>
            &ldquo;I don&apos;t know&rdquo; is a real answer here. A lucky guess would put something
            in the wrong place in your plan.
          </Hint>
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

export function Ring({
  value,
  caption,
  size = 68,
}: {
  value: number
  caption: string
  size?: number
}) {
  const stroke = size >= 60 ? 6 : 5
  const radius = size / 2 - stroke
  const circumference = 2 * Math.PI * radius
  const filled = Math.max(0, Math.min(1, value))

  return (
    <div className="relative shrink-0" style={{ height: size, width: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full -rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          className="fill-none stroke-paper-300"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          className="fill-none stroke-clay-500 transition-[stroke-dashoffset] duration-500"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - filled)}
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center font-mono text-[11px] font-medium text-ink-700">
        {caption}
      </span>
    </div>
  )
}
