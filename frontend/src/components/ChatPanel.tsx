/**
 * A persistent assistant, answering only from this learner's own plan.
 *
 * The suggested questions are not decoration: an empty chat box with a blinking
 * cursor gets ignored, whereas three concrete questions tell a visitor in one
 * glance what this thing is for and what it can answer.
 *
 * The citations under each answer are the substance — they resolve server-side
 * to the resources actually bound to this learner's plan, so a claim about a
 * course always arrives with the real, working link it came from.
 */

import { useEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api, type ChatReply } from '../lib/api'
import { useSession } from '../lib/store'

type Turn = { role: 'learner' | 'assistant'; text: string; reply?: ChatReply }

const SUGGESTIONS = [
  'What should I do first?',
  'Why is this in my plan?',
  'How many hours are left?',
]

export default function ChatPanel() {
  const learnerId = useSession((s) => s.learnerId)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const endRef = useRef<HTMLDivElement>(null)

  const ask = useMutation({
    mutationFn: (message: string) => api.chat(learnerId!, message),
    onSuccess: (reply) => setTurns((t) => [...t, { role: 'assistant', text: reply.reply, reply }]),
    onError: (error) =>
      setTurns((t) => [
        ...t,
        {
          role: 'assistant',
          text:
            error instanceof Error
              ? `I could not reach the plan just now — ${error.message}`
              : 'Something went wrong.',
        },
      ]),
  })

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns.length, ask.isPending])

  if (learnerId === null) return null

  function submit(message: string) {
    const text = message.trim()
    if (!text || ask.isPending) return
    setTurns((t) => [...t, { role: 'learner', text }])
    setDraft('')
    ask.mutate(text)
  }

  if (!open) {
    return (
      <button
        className="btn-primary fixed bottom-5 right-5 z-30 shadow-lift"
        onClick={() => setOpen(true)}
      >
        Ask about my plan
      </button>
    )
  }

  return (
    <aside className="fixed bottom-4 right-4 z-30 flex max-h-[72vh] w-[min(390px,calc(100vw-2rem))] flex-col overflow-hidden rounded-xl border border-paper-400 bg-paper-50 shadow-lift">
      <header className="flex items-center justify-between border-b border-paper-400 bg-paper-200 px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-ink-900">Ask about your plan</p>
          <p className="text-[11px] text-ink-400">Answers come only from your own plan</p>
        </div>
        <button
          className="btn-quiet px-2 py-1 text-base leading-none"
          onClick={() => setOpen(false)}
          aria-label="Close"
        >
          ✕
        </button>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {turns.length === 0 && (
          <div className="space-y-2">
            <p className="text-[13px] leading-relaxed text-ink-500">
              I can only see your goal, your plan and your progress — so I will say so plainly if
              you ask something outside that.
            </p>
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                className="block w-full rounded-lg border border-paper-400 bg-paper-100 px-3 py-2 text-left text-[13px] text-ink-700 hover:border-clay-300 hover:text-clay-700"
                onClick={() => submit(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        )}

        {turns.map((turn, index) => (
          <div key={index} className={turn.role === 'learner' ? 'text-right' : ''}>
            <div
              className={[
                'inline-block max-w-[92%] rounded-xl px-3 py-2 text-left text-[13px] leading-relaxed',
                turn.role === 'learner'
                  ? 'bg-clay-500 text-paper-50'
                  : 'border border-paper-400 bg-paper-200 text-ink-700',
              ].join(' ')}
            >
              {turn.text}
            </div>
            {turn.reply && turn.reply.citations.length > 0 && (
              <ul className="mt-1.5 space-y-1">
                {turn.reply.citations.slice(0, 3).map((citation) => (
                  <li key={citation.url}>
                    <a
                      className="break-words text-[12px] text-clay-600 underline decoration-clay-300 underline-offset-2 hover:decoration-clay-600"
                      href={citation.url}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      {citation.title} ↗
                    </a>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
        {ask.isPending && <p className="text-[13px] text-ink-400">Looking at your plan…</p>}
        <div ref={endRef} />
      </div>

      <form
        className="flex gap-2 border-t border-paper-400 p-3"
        onSubmit={(event) => {
          event.preventDefault()
          submit(draft)
        }}
      >
        <input
          className="field flex-1 py-2"
          placeholder="Ask a question…"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-label="Question"
        />
        <button className="btn-primary px-3" type="submit" disabled={ask.isPending || !draft.trim()}>
          Ask
        </button>
      </form>
    </aside>
  )
}
