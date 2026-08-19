/**
 * The path screen: the DAG beside a week-by-week timeline.
 *
 * Three things here carry the product's argument:
 * - the graph shows that the order is a *constraint*, not a ranking,
 * - each card's Why? chip exposes the computed provenance behind it,
 * - the hours slider recomputes the finish week live through `/whatif`, which
 *   writes nothing, so the learner can ask "what if" without committing to it.
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type PathDiff, type PathItem } from '../lib/api'
import { queryState } from '../lib/queryState'
import { useSession } from '../lib/store'
import { DegradedBanner, EmptyPanel, ErrorPanel, LoadingPanel } from '../components/states'
import SkillGraphView, { GraphLegend } from '../components/SkillGraphView'
import WhyChip from '../components/WhyChip'

export default function Path() {
  const learnerId = useSession((s) => s.learnerId)!
  const queryClient = useQueryClient()
  const [diff, setDiff] = useState<{ diff: PathDiff; message: string } | null>(null)
  const [hours, setHours] = useState<number | null>(null)

  const path = useQuery({ queryKey: ['path', learnerId], queryFn: () => api.getPath(learnerId) })
  const graph = useQuery({ queryKey: ['graph', learnerId], queryFn: () => api.graph(learnerId) })

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
    mutationFn: (input: { type: Parameters<typeof api.sendEvent>[1]; payload: Record<string, unknown> }) =>
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
    ])
  }

  const pathState = queryState(path)
  if (!pathState.data) {
    return pathState.failure ? (
      <ErrorPanel error={pathState.failure} onRetry={pathState.retry} />
    ) : (
      <LoadingPanel label="Loading your path" rows={6} />
    )
  }

  const data = pathState.data
  const graphState = queryState(graph)
  if (!data.items.length) {
    return (
      <EmptyPanel
        title={data.path_id ? 'Nothing left to learn for this goal' : 'No path yet'}
        action={
          <button className="btn-primary" onClick={() => generate.mutate()} disabled={generate.isPending}>
            {generate.isPending ? 'Sequencing…' : 'Generate my path'}
          </button>
        }
      >
        {data.path_id
          ? 'Your measured mastery already covers every skill this goal requires. Change your goal to keep going.'
          : 'Generate one from your diagnostic results.'}
      </EmptyPanel>
    )
  }

  const byWeek = groupByWeek(data.items)
  const projected = whatIf.data

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label">Step 3 of 4 — version {data.version}</p>
          <h1 className="text-2xl font-bold tracking-tight">{data.goal_names.join(' and ')}</h1>
          <p className="mt-1 text-sm text-mist-500">
            {data.items.filter((i) => i.kind === 'resource').length} steps ·{' '}
            {data.total_hours}h total · finishes week{' '}
            <span className="font-mono text-mist-100">{data.finish_week}</span> at{' '}
            {data.hours_per_week}h/week
          </p>
        </div>
        <button className="btn-ghost" onClick={() => generate.mutate()} disabled={generate.isPending}>
          {generate.isPending ? 'Replanning…' : 'Replan from current state'}
        </button>
      </header>

      <DegradedBanner show={data.llm_degraded} />
      {diff && <DiffBanner {...diff} onDismiss={() => setDiff(null)} />}

      <section className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">Your route through the dependency graph</h2>
          <GraphLegend />
        </div>
        {graphState.data ? (
          <SkillGraphView payload={graphState.data} />
        ) : graphState.failure ? (
          <ErrorPanel error={graphState.failure} onRetry={graphState.retry} />
        ) : (
          <LoadingPanel label="Drawing the graph" rows={2} />
        )}
      </section>

      <section className="card p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-sm font-semibold">What if I had more time?</h2>
            <p className="mt-0.5 text-xs text-mist-500">
              Recomputed live. Nothing is saved until you replan.
            </p>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <p className="label">finish week</p>
              <p className="font-mono text-2xl font-bold text-ember-400">
                {projected?.finish_week ?? data.finish_week}
              </p>
            </div>
            {projected && projected.finish_week !== data.finish_week && (
              <span className="chip border-ember-500/50 text-ember-400">
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
            className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-ink-700 accent-ember-500"
            aria-label="Hours per week"
          />
          <span className="w-24 shrink-0 text-right font-mono text-sm">
            {hours ?? data.hours_per_week} h/wk
          </span>
        </div>
      </section>

      <section className="space-y-6">
        {byWeek.map(([week, items]) => (
          <div key={week}>
            <div className="mb-2 flex items-center gap-3">
              <span className="chip border-ember-500/40 font-mono text-ember-400">week {week}</span>
              <span className="text-xs text-mist-500">
                {items.reduce((sum, i) => sum + i.est_hours, 0).toFixed(1)}h starting this week
              </span>
              <div className="edge-rule flex-1" />
            </div>
            <div className="space-y-3">
              {items.map((item) => (
                <ItemCard
                  key={`${item.id}-${item.order_index}`}
                  item={item}
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
  busy,
  onEvent,
}: {
  item: PathItem
  busy: boolean
  onEvent: (type: Parameters<typeof api.sendEvent>[1], payload: Record<string, unknown>) => void
}) {
  const done = item.status === 'done'

  if (item.kind === 'milestone') {
    return (
      <div className="rounded-xl border border-dashed border-ember-500/40 bg-ember-500/5 p-4">
        <p className="text-sm font-semibold text-ember-400">
          Checkpoint — {item.provenance.milestone?.track} block
        </p>
        <p className="mt-1 text-sm text-mist-500">{item.rationale_text}</p>
        <button
          className="btn-ghost mt-3"
          disabled={busy}
          onClick={() => onEvent('milestone_failed', { item_id: item.id })}
        >
          I failed this checkpoint
        </button>
      </div>
    )
  }

  return (
    <article className={`card p-4 ${done ? 'opacity-60' : ''}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold">{item.skill_name}</h3>
          {item.course ? (
            <a
              className="mt-0.5 inline-block break-words text-sm text-ember-400 underline-offset-2 hover:underline"
              href={item.course.url}
              target="_blank"
              rel="noreferrer noopener"
            >
              {item.course.title} ↗
            </a>
          ) : (
            <p className="mt-0.5 text-sm text-mist-500">No catalog resource — self-study</p>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {item.course && <span className="chip">{item.course.provider}</span>}
          {item.course && <span className="chip">{item.course.format}</span>}
          {item.course && (
            <span className={`chip ${item.course.cost === 'free' ? 'text-signal-ok' : ''}`}>
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

      <p className="mt-2 text-sm leading-relaxed text-mist-300">{item.rationale_text}</p>

      <WhyChip item={item} />

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          className="chip hover:text-mist-100"
          disabled={busy || done}
          onClick={() => onEvent('completed_item', { item_id: item.id })}
        >
          {done ? '✓ done' : 'Mark done'}
        </button>
        <button
          className="chip hover:text-mist-100"
          disabled={busy}
          onClick={() => onEvent('too_easy', { item_id: item.id })}
        >
          Too easy
        </button>
        <button
          className="chip hover:text-mist-100"
          disabled={busy}
          onClick={() => onEvent('too_hard', { item_id: item.id })}
        >
          Too hard
        </button>
        {item.alternatives.length > 0 && (
          <button
            className="chip hover:text-mist-100"
            disabled={busy}
            onClick={() => onEvent('resource_disliked', { item_id: item.id })}
          >
            Different resource ({item.alternatives.length} more)
          </button>
        )}
      </div>
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
    diff.added.length ? `+${diff.added.length} item${diff.added.length === 1 ? '' : 's'}` : null,
    diff.removed.length ? `−${diff.removed.length} item${diff.removed.length === 1 ? '' : 's'}` : null,
    diff.moved_weeks.length ? `${diff.moved_weeks.length} moved` : null,
    diff.resource_swapped.length ? `${diff.resource_swapped.length} resource swapped` : null,
    diff.finish_week_delta !== 0
      ? `finish ${diff.finish_week_delta > 0 ? '+' : ''}${diff.finish_week_delta} week${
          Math.abs(diff.finish_week_delta) === 1 ? '' : 's'
        }`
      : null,
  ].filter(Boolean)

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-ember-500/40 bg-ember-500/10 px-4 py-3 text-sm">
      <strong className="font-semibold text-ember-400">Path updated</strong>
      <span className="text-mist-300">{message}</span>
      {parts.length > 0 && (
        <span className="font-mono text-xs text-mist-300">{parts.join(' · ')}</span>
      )}
      {diff.unchanged && <span className="text-mist-500">No structural change.</span>}
      <button className="ml-auto text-xs text-mist-500 hover:text-mist-100" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  )
}
