/**
 * The Why? chip: provenance rendered as structured rows, not a paragraph.
 *
 * This is the explainability feature and the debugging tool at once. Every row
 * below is a field the planner computed *before* any language was generated, so
 * a claim on this panel can be checked against the database. A paragraph could
 * not be checked, which is exactly why the weak version of "explain why" proves
 * nothing.
 */

import { useState } from 'react'
import type { PathItem } from '../lib/api'

export default function WhyChip({ item }: { item: PathItem }) {
  const [open, setOpen] = useState(false)
  const p = item.provenance

  return (
    <div className="mt-3">
      <button
        className="chip hover:border-ember-500/60 hover:text-mist-100"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden>{open ? '▾' : '▸'}</span> Why this, why now?
      </button>

      {open && (
        <div className="mt-3 animate-fade-up space-y-px overflow-hidden rounded-lg border border-ink-700 bg-ink-950/60">
          <Row label="Skill">
            <span className="font-medium text-mist-100">{item.skill_name}</span>
            {p.track && <span className="ml-2 text-mist-500">{p.track}</span>}
          </Row>

          {p.why_needed && (
            <Row label="Why it is needed">
              {p.why_needed.is_goal ? (
                <>This is your goal itself.</>
              ) : p.why_needed.path_to_goal.length ? (
                <span className="flex flex-wrap items-center gap-1.5">
                  <code className="font-mono text-xs text-ember-400">{item.skill_id}</code>
                  {p.why_needed.path_to_goal.map((step) => (
                    <span key={step} className="flex items-center gap-1.5">
                      <span aria-hidden className="text-mist-500">
                        →
                      </span>
                      <code className="font-mono text-xs">{step}</code>
                    </span>
                  ))}
                  <span className="text-mist-500">→ {p.why_needed.goal}</span>
                </span>
              ) : (
                <>Required by {p.why_needed.goal}.</>
              )}
            </Row>
          )}

          {p.your_level && (
            <Row label="Your level">
              <span className="font-mono text-mist-100">
                {Math.round(p.your_level.score * 100)}%
              </span>
              <span className="ml-2 text-mist-500">
                from {sourceLabel(p.your_level.source)}; {Math.round(p.your_level.threshold * 100)}%
                is the bar for skipping it
              </span>
              {p.your_level.evidence_q_ids.length > 0 && (
                <span className="ml-2 text-mist-500">
                  (questions {p.your_level.evidence_q_ids.join(', ')})
                </span>
              )}
            </Row>
          )}

          {p.why_this_resource?.title && (
            <Row label="Why this resource">
              <span className="text-mist-100">{p.why_this_resource.title}</span>
              {p.why_this_resource.beat_alternatives > 0 && (
                <span className="ml-2 text-mist-500">
                  chosen over {p.why_this_resource.beat_alternatives} alternative
                  {p.why_this_resource.beat_alternatives === 1 ? '' : 's'}
                </span>
              )}
              {p.why_this_resource.reasons.length > 0 && (
                <ul className="mt-1.5 space-y-0.5">
                  {p.why_this_resource.reasons.map((reason) => (
                    <li key={reason} className="text-mist-300">
                      <span aria-hidden className="mr-1.5 text-ember-500">
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
            <Row label="Placement">
              Week <span className="font-mono text-mist-100">{p.placement.week}</span>,{' '}
              {p.placement.est_hours}h.
              {p.placement.unlock_count > 0 && (
                <span className="ml-1 text-mist-500">
                  Unlocks {p.placement.unlock_count} later skill
                  {p.placement.unlock_count === 1 ? '' : 's'}
                  {p.placement.unlocks.length > 0 && (
                    <> — {p.placement.unlocks.slice(0, 3).join(', ')}</>
                  )}
                  .
                </span>
              )}
            </Row>
          )}

          {p.why_this_resource?.score != null && (
            <Row label="Score">
              <span className="font-mono text-mist-100">
                {p.why_this_resource.score.toFixed(3)}
              </span>
              <span className="ml-2 text-mist-500">
                0.45·semantic + 0.20·level + 0.15·format + 0.10·cost + 0.10·rating
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
    <div className="grid gap-1 bg-ink-900/60 px-4 py-3 sm:grid-cols-[150px_minmax(0,1fr)] sm:gap-4">
      <div className="label pt-0.5">{label}</div>
      <div className="min-w-0 break-words text-sm text-mist-300">{children}</div>
    </div>
  )
}

function sourceLabel(source: string): string {
  switch (source) {
    case 'diagnostic':
      return 'your diagnostic'
    case 'milestone':
      return 'a milestone check'
    case 'self':
      return 'your own estimate (capped at 40%)'
    default:
      return 'no measurement yet'
  }
}
