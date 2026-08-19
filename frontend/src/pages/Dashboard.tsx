/**
 * Dashboard: progress, hours, mastery spread, milestones, next actions, feed.
 *
 * Everything on this screen is derived from rows the rest of the app already
 * wrote, so it cannot disagree with the path screen. The activity feed is a
 * direct read of the event log — which is also why nothing in this system
 * changes state silently.
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
import { api } from '../lib/api'
import { queryState } from '../lib/queryState'
import { useSession } from '../lib/store'
import { EmptyPanel, ErrorPanel, LoadingPanel } from '../components/states'
import { Ring } from './Diagnostic'

const EVENT_LABELS: Record<string, string> = {
  intake_committed: 'Profile created and goal resolved',
  path_generated: 'Learning path generated',
  diagnostic_answered: 'Diagnostic question answered',
  too_easy: 'Marked a step as too easy',
  too_hard: 'Marked a step as too hard',
  milestone_failed: 'Failed a checkpoint',
  completed_item: 'Completed a step',
  resource_disliked: 'Asked for a different resource',
  behind_schedule: 'Reported falling behind',
  goal_changed: 'Changed the goal',
  chat_question: 'Asked the assistant a question',
}

export default function Dashboard() {
  const learnerId = useSession((s) => s.learnerId)!
  const dashboard = useQuery({
    queryKey: ['dashboard', learnerId],
    queryFn: () => api.dashboard(learnerId),
  })

  // Guard on the absence of data, never on `isLoading` -- see lib/queryState.
  const state = queryState(dashboard)
  if (!state.data) {
    return state.failure ? (
      <ErrorPanel error={state.failure} onRetry={state.retry} />
    ) : (
      <LoadingPanel label="Loading your dashboard" rows={6} />
    )
  }

  const data = state.data
  if (data.items_total === 0) {
    return (
      <EmptyPanel title="No path to report on yet">
        Generate a learning path and your progress will appear here.
      </EmptyPanel>
    )
  }

  const radar = data.mastery_radar.map((row) => ({
    track: row.track.replace(/-/g, ' '),
    mastery: Math.round(row.mastery * 100),
  }))

  return (
    <div className="space-y-5">
      <header>
        <p className="label">Step 4 of 4</p>
        <h1 className="text-2xl font-bold tracking-tight">{data.goal_names.join(' and ')}</h1>
      </header>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Progress">
          <div className="flex items-center gap-4">
            <Ring value={data.progress_pct / 100} label={`${Math.round(data.progress_pct)}%`} />
            <p className="text-sm text-mist-500">
              {data.items_done} of {data.items_total} steps done
            </p>
          </div>
        </Stat>

        <Stat label="Hours">
          <p className="font-mono text-3xl font-bold">{data.hours_done}</p>
          <p className="mt-1 text-sm text-mist-500">{data.hours_remaining}h remaining</p>
          <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-ink-700">
            <div
              className="h-full rounded-full bg-ember-500"
              style={{
                width: `${
                  (100 * data.hours_done) / Math.max(1, data.hours_done + data.hours_remaining)
                }%`,
              }}
            />
          </div>
        </Stat>

        <Stat label="Timeline">
          <p className="font-mono text-3xl font-bold">
            {data.current_week}
            <span className="text-lg text-mist-500"> / {data.finish_week}</span>
          </p>
          <p className="mt-1 text-sm text-mist-500">current week of {data.finish_week}</p>
        </Stat>

        <Stat label="Checkpoints">
          <p className="font-mono text-3xl font-bold">{data.milestones.length}</p>
          <p className="mt-1 text-sm text-mist-500">
            {data.milestones.map((m) => `w${m.week}`).join(', ') || 'none scheduled'}
          </p>
        </Stat>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <h2 className="text-sm font-semibold">Mastery by track</h2>
          <p className="mt-0.5 text-xs text-mist-500">
            Averaged over the skills your goal actually requires, not the whole graph.
          </p>
          <div className="mt-4 h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radar} outerRadius="72%">
                <PolarGrid stroke="#26355e" />
                <PolarAngleAxis dataKey="track" tick={{ fill: '#7784a6', fontSize: 11 }} />
                <PolarRadiusAxis domain={[0, 100]} tick={{ fill: '#37497a', fontSize: 10 }} />
                <Radar
                  dataKey="mastery"
                  stroke="#f59331"
                  fill="#f59331"
                  fillOpacity={0.28}
                  isAnimationActive={false}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="card p-5">
          <h2 className="text-sm font-semibold">Next 3 actions</h2>
          <ol className="mt-3 space-y-3">
            {data.next_actions.map((item, index) => (
              <li key={item.id ?? index} className="rounded-lg border border-ink-700 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold">{item.skill_name}</p>
                    {item.course && (
                      <a
                        className="break-words text-xs text-ember-400 underline-offset-2 hover:underline"
                        href={item.course.url}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {item.course.title} ↗
                      </a>
                    )}
                  </div>
                  <span className="chip shrink-0 font-mono">w{item.week_number}</span>
                </div>
              </li>
            ))}
            {data.next_actions.length === 0 && (
              <li className="text-sm text-mist-500">Everything is complete.</li>
            )}
          </ol>
        </div>
      </section>

      <section className="card p-5">
        <h2 className="text-sm font-semibold">Activity</h2>
        <p className="mt-0.5 text-xs text-mist-500">
          Every state change is appended to the event log. Nothing here is reconstructed.
        </p>
        <ul className="mt-3 space-y-2">
          {data.activity.map((entry) => (
            <li key={entry.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-sm">
              <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-ember-500" />
              <span className="text-mist-300">
                {EVENT_LABELS[entry.type] ?? entry.type.replace(/[:_]/g, ' ')}
              </span>
              <time className="font-mono text-xs text-mist-500" dateTime={entry.created_at}>
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
