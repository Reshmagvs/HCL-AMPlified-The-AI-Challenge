/**
 * A persistent assistant grounded strictly in this learner's own path.
 *
 * The citations under each answer are the point: they are resolved server-side
 * from the resources actually bound to this learner's path, so a claim about a
 * course is always accompanied by the real, working link it came from.
 */

import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api, type ChatReply } from '../lib/api'
import { useSession } from '../lib/store'

type Turn = { role: 'learner' | 'assistant'; text: string; reply?: ChatReply }

export default function ChatPanel() {
  const learnerId = useSession((s) => s.learnerId)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])

  const ask = useMutation({
    mutationFn: (message: string) => api.chat(learnerId!, message),
    onSuccess: (reply) => setTurns((t) => [...t, { role: 'assistant', text: reply.reply, reply }]),
    onError: (error) =>
      setTurns((t) => [
        ...t,
        { role: 'assistant', text: error instanceof Error ? error.message : 'Something went wrong.' },
      ]),
  })

  if (learnerId === null) return null

  function submit() {
    const message = draft.trim()
    if (!message || ask.isPending) return
    setTurns((t) => [...t, { role: 'learner', text: message }])
    setDraft('')
    ask.mutate(message)
  }

  if (!open) {
    return (
      <button
        className="btn-primary fixed bottom-5 right-5 z-30 shadow-lg shadow-black/40"
        onClick={() => setOpen(true)}
      >
        Ask about my path
      </button>
    )
  }

  return (
    <aside className="fixed bottom-4 right-4 z-30 flex max-h-[70vh] w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-900 shadow-2xl shadow-black/50">
      <header className="flex items-center justify-between border-b border-ink-700 px-4 py-2.5">
        <div>
          <p className="text-sm font-semibold">Study assistant</p>
          <p className="text-[11px] text-mist-500">Answers only from your own plan</p>
        </div>
        <button
          className="text-mist-500 hover:text-mist-100"
          onClick={() => setOpen(false)}
          aria-label="Close"
        >
          ✕
        </button>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {turns.length === 0 && (
          <p className="text-sm text-mist-500">
            Try “what should I do first?”, “why is linear algebra in my path?”, or “how many hours
            are left?”
          </p>
        )}
        {turns.map((turn, index) => (
          <div key={index} className={turn.role === 'learner' ? 'text-right' : ''}>
            <div
              className={[
                'inline-block max-w-[92%] rounded-xl px-3 py-2 text-left text-sm leading-relaxed',
                turn.role === 'learner' ? 'bg-ember-500 text-ink-950' : 'bg-ink-800',
              ].join(' ')}
            >
              {turn.text}
            </div>
            {turn.reply && turn.reply.citations.length > 0 && (
              <ul className="mt-1.5 space-y-1">
                {turn.reply.citations.slice(0, 3).map((citation) => (
                  <li key={citation.url}>
                    <a
                      className="break-words text-xs text-ember-400 underline-offset-2 hover:underline"
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
        {ask.isPending && <p className="text-sm text-mist-500">Thinking…</p>}
      </div>

      <form
        className="flex gap-2 border-t border-ink-700 p-3"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <input
          className="min-w-0 flex-1 rounded-lg border border-ink-600 bg-ink-950 px-3 py-2 text-sm placeholder:text-mist-500/70"
          placeholder="Ask about your path…"
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
