/**
 * Step 3 — the plan itself.
 *
 * The screen is ordered by how a learner actually uses it: what am I doing this
 * week, why, and what happens if my life changes. So the week timeline leads,
 * the graph sits behind a toggle for people who want the shape of the thing, and
 * the "what if" control is presented as a question rather than a setting.
 *
 * The feedback buttons on each card are the adaptation surface. They are
 * labelled in the learner's language ("I already know this") rather than the
 * system's ("too_easy"), and the first card carries a hint explaining that
 * pressing them rebuilds the plan.
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type PathDiff, type PathEventType, type PathItem } from '../lib/api'
import { queryState } from '../lib/queryState'
import { useSession } from '../lib/store'
import { Callout, DegradedBanner, EmptyPanel, ErrorPanel, Hint, LoadingPanel } from '../components/states'
import SkillMap, { GraphLegend } from '../components/SkillMap'
import WhyChip from '../components/WhyChip'

export default function Path() {
  const learnerId = useSession((s) => s.learnerId)!
  const queryClient = useQueryClient()
  const [diff, setDiff] = useState<{ diff: PathDiff; message: string } | null>(null)
  const [hours, setHours] = useState<number | null>(null)
  const [showGraph, setShowGraph] = useState(false)

  const path = useQuery({ queryKey: ['path', learnerId], queryFn: () => api.getPath(learnerId) })
  const graph = useQuery({
    queryKey: ['graph', learnerId],
    queryFn: () => api.graph(learnerId),
    enabled: showGraph,
  })

  useEffect(() => {
    if (path.data && hours === null) setHours(path.data.hours_per_week)
  }, [path.data, hours])

  const whatIf = useQuery({
    queryKey: ['whatif', learnerId, hours],
    queryFn: () => api.whatIf(learnerId, hours!),
    enabled: hours !== null && !!path.data && hours !== path.data.hours_per_week,
  })

  const generate = useMutation({
    mutationFn: () => api.generatePath(learnerId),
    onSuccess: () => void refreshAll(),
  })

  const event = useMutation({
    mutationFn: (input: { type: PathEventType; payload: Record<string, unknown> }) =>
      api.sendEvent(learnerId, input.type, input.payload),
    onSuccess: (result) => {
      setDiff({ diff: result.diff, message: result.message })
      void refreshAll()
    },
  })

  function refreshAll() {
    return Promise.all([
      queryClient.invalidateQueries({ queryKey: ['path', learnerId] }),
      queryClient.invalidateQueries({ queryKey: ['graph', learnerId] }),
      queryClient.invalidateQueries({ queryKey: ['dashboard', learnerId] }),
      queryClient.invalidateQueries({ queryKey: ['whatif', learnerId] }),
    ])
  }

  const pathState = queryState(path)
  if (!pathState.data) {
    return pathState.failure ? (
      <ErrorPanel error={pathState.failure} onRetry={pathState.retry} />
    ) : (
      <LoadingPanel label="Loading your plan" rows={6} />
    )
  }

  const data = pathState.data
  if (!data.items.length) {
    return (
      <EmptyPanel
        title={data.path_id ? 'Nothing left to learn for this goal' : 'No plan yet'}
        action={
          <button className="btn-primary" onClick={() => generate.mutate()} disabled={generate.isPending}>
            {generate.isPending ? 'Working it out…' : 'Build my plan'}
          </button>
        }
      >
        {data.path_id
          ? 'Your answers already cover every skill this goal needs. Pick a bigger goal to keep going.'
          : 'Build one from your placement check and it will appear here.'}
      </EmptyPanel>
    )
  }

  const byWeek = groupByWeek(data.items)
  const projected = whatIf.data
  const graphState = queryState(graph)
  const steps = data.items.filter((i) => i.kind === 'resource').length

  return (
    <div className="mx-auto max-w-[1150px] space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label">Step 3 of 4 · version {data.version}</p>
          <h1 className="mt-1 text-2xl">{data.goal_names.join(' and ')}</h1>
          <p className="mt-1.5 text-sm text-ink-500">
            <strong className="font-semibold text-ink-900">{steps} steps</strong> · {data.total_hours}h
            of study · finishes{' '}
            <strong className="font-semibold text-ink-900">week {data.finish_week}</strong> at{' '}
            {data.hours_per_week}h a week
          </p>
        </div>
        <button
          className="btn-secondary"
          onClick={() => generate.mutate()}
          disabled={generate.isPending}
          title="Rebuild the plan from everything known about you right now"
        >
          {generate.isPending ? 'Rebuilding…' : 'Rebuild plan'}
        </button>
      </header>

      <DegradedBanner show={data.llm_degraded} />
      {diff && <DiffBanner {...diff} onDismiss={() => setDiff(null)} />}

      <Callout tone="accent" title="How to read this">
        Steps are in the only order that works — nothing appears before the things it depends on.
        Open <strong>Why this, and why now?</strong> under any step to see exactly how it was
        chosen, and use the buttons on a card if it is wrong for you.
      </Callout>

      <section className="card p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-[15px] font-semibold text-ink-900">What if I had more time?</h2>
            <p className="mt-0.5 text-[13px] text-ink-500">
              Drag to see the effect. Nothing is saved unless you rebuild.
            </p>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <p className="label">Finishes</p>
              <p className="font-mono text-2xl font-bold text-clay-600">
                week {projected?.finish_week ?? data.finish_week}
              </p>
            </div>
            {projected && projected.finish_week !== data.finish_week && (
              <span className="chip-accent whitespace-nowrap">
                {projected.finish_week < data.finish_week ? '−' : '+'}
                {Math.abs(data.finish_week - projected.finish_week)} weeks
              </span>
            )}
          </div>
        </div>
        <div className="mt-4 flex items-center gap-4">
          <input
            type="range"
            min={1}
            max={40}
            step={1}
            value={hours ?? data.hours_per_week}
            onChange={(e) => setHours(Number(e.target.value))}
            className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-paper-300 accent-clay-500"
            aria-label="Hours per week"
          />
          <span className="w-24 shrink-0 text-right font-mono text-sm text-ink-700">
            {hours ?? data.hours_per_week} h/week
          </span>
        </div>
      </section>

      <section>
        <button
          className="btn-secondary w-full sm:w-auto"
          onClick={() => setShowGraph((value) => !value)}
          aria-expanded={showGraph}
        >
          {showGraph ? 'Hide the skill map' : 'Show the skill map'}
        </button>
        {showGraph && (
          <div className="mt-3 space-y-2">
            <GraphLegend />
            {graphState.data ? (
              <SkillMap payload={graphState.data} />
            ) : graphState.failure ? (
              <ErrorPanel error={graphState.failure} onRetry={graphState.retry} />
            ) : (
              <LoadingPanel label="Drawing the map" rows={2} />
            )}
            <Hint>
              Left to right is dependency order. Your route is highlighted; everything faded is not
              needed for this goal.
            </Hint>
          </div>
        )}
      </section>

      <section className="space-y-7">
        {byWeek.map(([week, items], weekIndex) => (
          <div key={week}>
            <div className="mb-3 flex items-center gap-3">
              <span className="chip-accent font-mono">Week {week}</span>
              <span className="text-[12px] text-ink-400">
                {(data.week_load[String(week)] ?? 0).toFixed(1)}h of your {data.hours_per_week}h
              </span>
              <div className="rule flex-1" />
            </div>
            <div className="space-y-3">
              {items.map((item, itemIndex) => (
                <ItemCard
                  key={`${item.id}-${item.order_index}`}
                  item={item}
                  first={weekIndex === 0 && itemIndex === 0}
                  busy={event.isPending}
                  onEvent={(type, payload) => event.mutate({ type, payload })}
                />
              ))}
            </div>
          </div>
        ))}
      </section>

      {event.isError && <ErrorPanel error={event.error} onRetry={() => event.reset()} />}
    </div>
  )
}

function groupByWeek(items: PathItem[]): [number, PathItem[]][] {
  const map = new Map<number, PathItem[]>()
  items.forEach((item) => map.set(item.week_number, [...(map.get(item.week_number) ?? []), item]))
  return [...map.entries()].sort((a, b) => a[0] - b[0])
}

function ItemCard({
  item,
  first,
  busy,
  onEvent,
}: {
  item: PathItem
  first: boolean
  busy: boolean
  onEvent: (type: PathEventType, payload: Record<string, unknown>) => void
}) {
  const done = item.status === 'done'

  if (item.kind === 'milestone') {
    return (
      <div className="rounded-xl border border-dashed border-clay-300 bg-clay-50/60 p-4">
        <p className="text-sm font-semibold text-clay-700">
          Checkpoint — end of the {item.provenance.milestone?.track.replace(/-/g, ' ')} block
        </p>
        <p className="mt-1 text-[13px] leading-relaxed text-ink-500">{item.rationale_text}</p>
        <button
          className="btn-secondary mt-3 text-xs"
          disabled={busy}
          onClick={() => onEvent('milestone_failed', { item_id: item.id })}
        >
          I struggled with this checkpoint
        </button>
      </div>
    )
  }

  return (
    <article className={`card p-5 ${done ? 'opacity-60' : ''}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-[15px] font-semibold text-ink-900">
            {done && <span className="text-sage-500">✓</span>}
            {item.skill_name}
          </h3>
          {item.course ? (
            <a
              className="mt-1 inline-block break-words text-sm text-clay-600 underline decoration-clay-300 underline-offset-2 hover:decoration-clay-600"
              href={item.course.url}
              target="_blank"
              rel="noreferrer noopener"
            >
              {item.course.title} ↗
            </a>
          ) : (
            <p className="mt-1 text-sm text-ink-400">No catalogue resource — study this yourself</p>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {item.course && <span className="chip">{item.course.provider}</span>}
          {item.course?.discovered && (
            <span
              className="chip chip-accent"
              title="Found by searching the web for this skill, then fetched and checked. Its title, provider and cost were read from the page itself; the reading time is an estimate."
            >
              found for you
            </span>
          )}
          {item.course && <span className="chip">{item.course.format}</span>}
          {item.course && (
            <span className={`chip ${item.course.cost === 'free' ? 'text-sage-700' : ''}`}>
              {item.course.cost}
            </span>
          )}
          <span className="chip font-mono" title="Time budgeted for this skill">
            {item.est_hours}h
          </span>
          {item.course && item.course.duration_hours > item.est_hours && (
            <span
              className="chip"
              title="The resource is longer because it covers several skills; only the part you need is scheduled."
            >
              of {item.course.duration_hours}h
            </span>
          )}
        </div>
      </div>

      <p className="mt-2.5 text-[14px] leading-relaxed text-ink-700">{item.rationale_text}</p>

      <WhyChip item={item} />

      <div className="mt-4 flex flex-wrap gap-2 border-t border-paper-300 pt-3">
        <button
          className="btn-quiet text-xs"
          disabled={busy || done}
          onClick={() => onEvent('completed_item', { item_id: item.id })}
        >
          {done ? '✓ Done' : 'Mark as done'}
        </button>
        <button
          className="btn-quiet text-xs"
          disabled={busy}
          title="Removes this step and moves everything after it earlier"
          onClick={() => onEvent('too_easy', { item_id: item.id })}
        >
          I already know this
        </button>
        <button
          className="btn-quiet text-xs"
          disabled={busy}
          title="Adds the groundwork this step assumes, before it"
          onClick={() => onEvent('too_hard', { item_id: item.id })}
        >
          This is too hard
        </button>
        {item.alternatives.length > 0 && (
          <button
            className="btn-quiet text-xs"
            disabled={busy}
            title={`Swap to the next best of ${item.alternatives.length} alternatives`}
            onClick={() => onEvent('resource_disliked', { item_id: item.id })}
          >
            Show me a different resource
          </button>
        )}
      </div>

      {first && (
        <div className="mt-2">
          <Hint>These buttons rebuild your plan straight away and show you what changed.</Hint>
        </div>
      )}
    </article>
  )
}

function DiffBanner({
  diff,
  message,
  onDismiss,
}: {
  diff: PathDiff
  message: string
  onDismiss: () => void
}) {
  const parts = [
    diff.added.length ? `${diff.added.length} added` : null,
    diff.removed.length ? `${diff.removed.length} removed` : null,
    diff.moved_weeks.length ? `${diff.moved_weeks.length} moved` : null,
    diff.resource_swapped.length ? `${diff.resource_swapped.length} swapped` : null,
    diff.finish_week_delta !== 0
      ? `finishes ${Math.abs(diff.finish_week_delta)} week${
          Math.abs(diff.finish_week_delta) === 1 ? '' : 's'
        } ${diff.finish_week_delta > 0 ? 'later' : 'earlier'}`
      : null,
  ].filter(Boolean)

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-sage-500/40 bg-sage-100 px-4 py-3 text-sm">
      <strong className="font-semibold text-sage-700">Plan updated</strong>
      <span className="text-ink-700">{message}</span>
      {parts.length > 0 && <span className="font-mono text-[12px] text-ink-500">{parts.join(' · ')}</span>}
      {diff.unchanged && <span className="text-ink-400">Nothing needed to move.</span>}
      <button className="btn-quiet ml-auto text-xs" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  )
}
