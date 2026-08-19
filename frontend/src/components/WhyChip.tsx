/**
 * "Why this?" — provenance rendered as structured rows, not a paragraph.
 *
 * This is the explainability feature and the debugging tool at once. Every row
 * is a field the planner computed *before* any sentence was written, so each
 * claim can be checked against the database. A paragraph could not be checked,
 * which is exactly why the weak version of "explain why" proves nothing.
 *
 * It is closed by default but labelled with what it will show, because an
 * unlabelled disclosure triangle is never opened by a first-time visitor.
 */

import { useState } from 'react'
import type { PathItem } from '../lib/api'

export default function WhyChip({ item }: { item: PathItem }) {
  const [open, setOpen] = useState(false)
  const p = item.provenance

  return (
    <div className="mt-3">
      <button
        className={`chip transition-colors hover:border-clay-300 hover:text-clay-700 ${
          open ? 'border-clay-300 bg-clay-50 text-clay-700' : ''
        }`}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden className="text-[10px]">
          {open ? '▼' : '▶'}
        </span>
        Why this, and why now?
      </button>

      {open && (
        <div className="mt-3 animate-fade-up overflow-hidden rounded-xl border border-paper-400">
          <p className="border-b border-paper-400 bg-paper-200 px-4 py-2 text-[12px] leading-snug text-ink-500">
            Everything below was calculated before any sentence was written — you can check each
            line against the plan itself.
          </p>

          <Row label="Skill">
            <span className="font-medium text-ink-900">{item.skill_name}</span>
            {p.track && <span className="ml-2 text-ink-400">{p.track.replace(/-/g, ' ')}</span>}
          </Row>

          {p.why_needed && (
            <Row label="Why you need it">
              {p.why_needed.is_goal ? (
                <>This is the goal itself.</>
              ) : p.why_needed.path_to_goal.length ? (
                <span className="flex flex-wrap items-center gap-1.5">
                  <code className="rounded bg-clay-50 px-1.5 py-0.5 font-mono text-[11px] text-clay-700">
                    {item.skill_id}
                  </code>
                  {p.why_needed.path_to_goal.map((step) => (
                    <span key={step} className="flex items-center gap-1.5">
                      <span aria-hidden className="text-ink-300">
                        →
                      </span>
                      <code className="rounded bg-paper-200 px-1.5 py-0.5 font-mono text-[11px]">
                        {step}
                      </code>
                    </span>
                  ))}
                  <span className="text-ink-400">→ {p.why_needed.goal}</span>
                </span>
              ) : (
                <>Required by {p.why_needed.goal}.</>
              )}
            </Row>
          )}

          {p.your_level && (
            <Row label="Where you are">
              <span className="font-mono font-medium text-ink-900">
                {Math.round(p.your_level.score * 100)}%
              </span>
              <span className="ml-2 text-ink-400">
                from {sourceLabel(p.your_level.source)} · {Math.round(p.your_level.threshold * 100)}%
                would skip it
              </span>
              {p.your_level.evidence_q_ids.length > 0 && (
                <span className="ml-2 text-ink-400">
                  (question{p.your_level.evidence_q_ids.length === 1 ? '' : 's'}{' '}
                  {p.your_level.evidence_q_ids.join(', ')})
                </span>
              )}
            </Row>
          )}

          {p.why_this_resource?.title && (
            <Row label="Why this resource">
              <span className="font-medium text-ink-900">{p.why_this_resource.title}</span>
              {p.why_this_resource.beat_alternatives > 0 && (
                <span className="ml-2 text-ink-400">
                  picked over {p.why_this_resource.beat_alternatives} other
                  {p.why_this_resource.beat_alternatives === 1 ? '' : 's'}
                </span>
              )}
              {p.why_this_resource.reasons.length > 0 && (
                <ul className="mt-1.5 space-y-0.5">
                  {p.why_this_resource.reasons.map((reason) => (
                    <li key={reason} className="flex gap-2 text-ink-500">
                      <span aria-hidden className="text-clay-500">
                        ·
                      </span>
                      {reason}
                    </li>
                  ))}
                </ul>
              )}
            </Row>
          )}

          {p.placement && (
            <Row label="Where it sits">
              Week <span className="font-mono font-medium text-ink-900">{p.placement.week}</span>,{' '}
              {p.placement.est_hours}h budgeted.
              {p.placement.unlock_count > 0 && (
                <span className="ml-1 text-ink-400">
                  Opens up {p.placement.unlock_count} later skill
                  {p.placement.unlock_count === 1 ? '' : 's'}
                  {p.placement.unlocks.length > 0 && <> — {p.placement.unlocks.slice(0, 3).join(', ')}</>}
                  .
                </span>
              )}
            </Row>
          )}

          {p.why_this_resource?.score != null && (
            <Row label="Score">
              <span className="font-mono font-medium text-ink-900">
                {p.why_this_resource.score.toFixed(3)}
              </span>
              <span className="ml-2 text-ink-400">
                45% topic match · 20% level · 15% format · 10% cost · 10% rating
              </span>
            </Row>
          )}
        </div>
      )}
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 border-b border-paper-300 bg-paper-50 px-4 py-3 last:border-b-0 sm:grid-cols-[142px_minmax(0,1fr)] sm:gap-4">
      <div className="label pt-0.5">{label}</div>
      <div className="min-w-0 break-words text-[13px] leading-relaxed text-ink-700">{children}</div>
    </div>
  )
}

function sourceLabel(source: string): string {
  switch (source) {
    case 'diagnostic':
      return 'your answers'
    case 'milestone':
      return 'a checkpoint'
    case 'self':
      return 'your own estimate (capped at 40%)'
    default:
      return 'no check yet'
  }
}
