/**
 * Step 4 — progress, and what to do next.
 *
 * Everything here is derived from rows the rest of the app already wrote, so it
 * cannot disagree with the plan screen. The activity feed is a direct read of
 * the event log, which is also why nothing in this system changes state
 * silently.
 *
 * The "next three actions" block is deliberately first among the detail panels:
 * a dashboard that shows a learner statistics but not their next move has got
 * its priorities backwards.
 */

import { useQuery } from '@tanstack/react-query'
import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
} from 'recharts'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { queryState } from '../lib/queryState'
import { useSession } from '../lib/store'
import { EmptyPanel, ErrorPanel, Hint, LoadingPanel } from '../components/states'
import { Ring } from './Diagnostic'

const EVENT_LABELS: Record<string, string> = {
  intake_committed: 'Goal set',
  path_generated: 'Plan built',
  diagnostic_answered: 'Answered a placement question',
  diagnostic_completed: 'Finished the placement check',
  too_easy: 'Marked a step as already known',
  too_hard: 'Marked a step as too hard',
  milestone_failed: 'Struggled with a checkpoint',
  completed_item: 'Completed a step',
  resource_disliked: 'Asked for a different resource',
  behind_schedule: 'Reported falling behind',
  goal_changed: 'Changed the goal',
  chat_question: 'Asked a question',
}

export default function Dashboard() {
  const learnerId = useSession((s) => s.learnerId)!
  const dashboard = useQuery({
    queryKey: ['dashboard', learnerId],
    queryFn: () => api.dashboard(learnerId),
  })

  const state = queryState(dashboard)
  if (!state.data) {
    return state.failure ? (
      <ErrorPanel error={state.failure} onRetry={state.retry} />
    ) : (
      <LoadingPanel label="Loading your progress" rows={6} />
    )
  }

  const data = state.data
  if (data.items_total === 0) {
    return (
      <EmptyPanel
        title="Nothing to report yet"
        action={
          <Link className="btn-primary" to="/path">
            Go to my plan
          </Link>
        }
      >
        Build a plan and your progress will appear here.
      </EmptyPanel>
    )
  }

  const radar = data.mastery_radar.map((row) => ({
    track: row.track.replace(/-/g, ' '),
    mastery: Math.round(row.mastery * 100),
  }))
  const hoursTotal = Math.max(1, data.hours_done + data.hours_remaining)

  return (
    <div className="mx-auto max-w-[1150px] space-y-6">
      <header>
        <p className="label">Step 4 of 4</p>
        <h1 className="mt-1 text-2xl">{data.goal_names.join(' and ')}</h1>
        <p className="mt-1.5 text-sm text-ink-500">
          You are in week {data.current_week} of {data.finish_week}.
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Progress">
          <div className="flex items-center gap-4">
            <Ring value={data.progress_pct / 100} caption={`${Math.round(data.progress_pct)}%`} />
            <p className="text-sm text-ink-500">
              {data.items_done} of {data.items_total} steps done
            </p>
          </div>
        </Stat>

        <Stat label="Study hours">
          <p className="font-mono text-3xl font-bold text-ink-900">{data.hours_done}</p>
          <p className="mt-1 text-sm text-ink-500">{data.hours_remaining}h still to go</p>
          <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-paper-300">
            <div
              className="h-full rounded-full bg-clay-500"
              style={{ width: `${(100 * data.hours_done) / hoursTotal}%` }}
            />
          </div>
        </Stat>

        <Stat label="Timeline">
          <p className="font-mono text-3xl font-bold text-ink-900">
            {data.current_week}
            <span className="text-lg text-ink-400"> / {data.finish_week}</span>
          </p>
          <p className="mt-1 text-sm text-ink-500">weeks</p>
        </Stat>

        <Stat label="Checkpoints">
          <p className="font-mono text-3xl font-bold text-ink-900">{data.milestones.length}</p>
          <p className="mt-1 text-sm text-ink-500">
            {data.milestones.length
              ? `weeks ${[...new Set(data.milestones.map((m) => m.week))].join(', ')}`
              : 'none scheduled'}
          </p>
        </Stat>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="card p-6">
          <h2 className="text-[15px] font-semibold text-ink-900">Do these next</h2>
          <p className="mt-0.5 text-[13px] text-ink-500">
            The first unfinished steps in your plan, in order.
          </p>
          <ol className="mt-4 space-y-3">
            {data.next_actions.map((item, index) => (
              <li key={item.id ?? index} className="rounded-lg border border-paper-400 bg-paper-50 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-ink-900">{item.skill_name}</p>
                    {item.course && (
                      <a
                        className="break-words text-[13px] text-clay-600 underline decoration-clay-300 underline-offset-2 hover:decoration-clay-600"
                        href={item.course.url}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {item.course.title} ↗
                      </a>
                    )}
                  </div>
                  <span className="chip shrink-0 font-mono">wk {item.week_number}</span>
                </div>
              </li>
            ))}
            {data.next_actions.length === 0 && (
              <li className="text-sm text-ink-500">Everything is complete. Well done.</li>
            )}
          </ol>
          <div className="mt-4">
            <Link className="btn-secondary w-full" to="/path">
              Open the full plan
            </Link>
          </div>
        </div>

        <div className="card p-6">
          <h2 className="text-[15px] font-semibold text-ink-900">Where you are strong</h2>
          <p className="mt-0.5 text-[13px] text-ink-500">
            Averaged over only the skills this goal actually needs.
          </p>
          <div className="mt-3 h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radar} outerRadius="72%">
                <PolarGrid stroke="#E3DACA" />
                <PolarAngleAxis dataKey="track" tick={{ fill: '#8B8175', fontSize: 11 }} />
                <PolarRadiusAxis domain={[0, 100]} tick={{ fill: '#C4B9A6', fontSize: 10 }} />
                <Radar
                  dataKey="mastery"
                  stroke="#B0563A"
                  fill="#B0563A"
                  fillOpacity={0.22}
                  isAnimationActive={false}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>
          <Hint>Low is normal at the start — this fills in as you complete steps and checkpoints.</Hint>
        </div>
      </section>

      <section className="card p-6">
        <h2 className="text-[15px] font-semibold text-ink-900">Everything that has happened</h2>
        <p className="mt-0.5 text-[13px] text-ink-500">
          Every change to your plan is recorded here, so nothing about it is a surprise.
        </p>
        <ul className="mt-4 space-y-2.5">
          {data.activity.map((entry) => (
            <li key={entry.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-sm">
              <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-clay-300" />
              <span className="text-ink-700">
                {EVENT_LABELS[entry.type] ?? entry.type.replace(/[:_]/g, ' ')}
              </span>
              <time className="font-mono text-[11px] text-ink-400" dateTime={entry.created_at}>
                {new Date(entry.created_at).toLocaleString()}
              </time>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="card p-5">
      <p className="label">{label}</p>
      <div className="mt-2">{children}</div>
    </div>
  )
}
