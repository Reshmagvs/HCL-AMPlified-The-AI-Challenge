import { useEffect, useState } from 'react'
import { api, type BuildStatus, type Coverage } from '../lib/api'

/**
 * The screen a learner sees when they ask for a subject nobody curated.
 *
 * Two design decisions carry it.
 *
 * **The wait is narrated, not spun.** Building a subject takes a couple of
 * minutes on a laptop CPU: the model writes a syllabus, then every skill in it
 * gets a live search whose results are all fetched and checked. A person will
 * sit through two minutes of "finding materials on Quantum Gates (4 of 9)".
 * Nobody sits through two minutes of a spinner, so the progress that is shown
 * is the real stage the server reports, never a fake animation.
 *
 * **The verdict is correctable.** Deciding whether a curriculum already covers
 * a goal is a judgement, and it is sometimes wrong. So when we think we already
 * teach it we say which skill we matched and offer to build the subject
 * properly anyway. A wrong guess a learner can see and overrule is worth more
 * than a confident one they cannot.
 */

const POLL_MS = 1200

type Props = {
  goalText: string
  /**
   * Whether a plan can be built for this goal right now. False only while a
   * subject is genuinely being constructed -- committing then would ask the
   * planner for a goal the graph does not contain yet.
   */
  onPlannableChange: (plannable: boolean) => void
}

export function TopicBuilder({ goalText, onPlannableChange }: Props) {
  const [coverage, setCoverage] = useState<Coverage | null>(null)
  const [build, setBuild] = useState<BuildStatus | null>(null)
  const [error, setError] = useState<string>('')
  const [checking, setChecking] = useState(true)

  // Check coverage whenever the goal settles.
  useEffect(() => {
    let cancelled = false
    const goal = goalText.trim()
    if (goal.length < 3) {
      setCoverage(null)
      setChecking(false)
      return
    }
    setChecking(true)
    api
      .coverage(goal)
      .then((result) => {
        if (cancelled) return
        setCoverage(result)
        setError('')
      })
      .catch((err: Error) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setChecking(false))
    return () => {
      cancelled = true
    }
  }, [goalText])

  // Follow a build that is running.
  useEffect(() => {
    if (!build || build.status === 'done' || build.status === 'failed') return
    const timer = setInterval(() => {
      api
        .buildStatus(goalText)
        .then(setBuild)
        .catch((err: Error) => setError(err.message))
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [build, goalText])

  // A goal is plannable unless a build is actually in flight. An uncovered
  // subject with no build started is still plannable -- resolution falls back
  // to the nearest curated skill, which is worse but not broken, and the
  // learner may reasonably choose not to wait.
  const building = build?.status === 'queued' || build?.status === 'running'
  useEffect(() => {
    onPlannableChange(!building)
  }, [building, onPlannableChange])

  const beginBuild = (force: boolean) => {
    setError('')
    api
      .startBuild(goalText, force)
      .then(setBuild)
      .catch((err: Error) => setError(err.message))
  }

  if (checking && !coverage) {
    return <p className="hint">Checking whether we already teach this…</p>
  }
  if (!coverage) return null

  if (error) {
    return (
      <div className="panel border-rust-300 bg-rust-50">
        <p className="text-sm text-rust-700">{error}</p>
      </div>
    )
  }

  // A build in flight, or one that just finished or failed.
  if (build && build.status !== 'none') {
    return <BuildProgress build={build} onRetry={() => beginBuild(true)} />
  }

  // Already built for this exact goal.
  if (coverage.already_built) {
    return (
      <p className="hint">
        We built <strong>{coverage.topic}</strong> for this goal earlier — your plan is ready
        straight away.
      </p>
    )
  }

  // We think we already teach it. Say what we matched, and offer the override.
  if (coverage.covered) {
    return (
      <div className="panel">
        <p className="text-sm text-ink-700">
          This looks like <strong>{coverage.matched_skill_name}</strong>, which is already in our
          curriculum — so your plan can start immediately.
        </p>
        <p className="hint mt-2">
          Not what you meant?{' '}
          <button type="button" className="btn-quiet underline" onClick={() => beginBuild(true)}>
            Build “{goalText.trim()}” as its own subject
          </button>{' '}
          instead. It takes a couple of minutes, once.
        </p>
      </div>
    )
  }

  // A genuinely new subject.
  return (
    <div className="panel">
      <p className="text-sm text-ink-700">
        We don’t teach this yet — {coverage.reason}.
      </p>
      {coverage.can_build ? (
        <>
          <p className="hint mt-2">
            We can build it: work out what the subject depends on, then search the web for
            material and check every page before using it. About two minutes, and only the first
            person to ask ever waits.
          </p>
          <button type="button" className="btn-primary mt-3" onClick={() => beginBuild(false)}>
            Build this subject
          </button>
        </>
      ) : (
        <p className="hint mt-2">{coverage.build_unavailable_reason}</p>
      )}
    </div>
  )
}

function BuildProgress({ build, onRetry }: { build: BuildStatus; onRetry: () => void }) {
  if (build.status === 'failed') {
    return (
      <div className="panel border-rust-300 bg-rust-50">
        <p className="text-sm font-medium text-rust-700">We couldn’t build this subject.</p>
        <p className="hint mt-1">{build.error}</p>
        <button type="button" className="btn-secondary mt-3" onClick={onRetry}>
          Try again
        </button>
      </div>
    )
  }

  if (build.status === 'done') {
    return (
      <div className="panel border-sage-300 bg-sage-50">
        <p className="text-sm text-ink-700">
          <strong>{build.topic}</strong> is ready — {build.skill_count} skills, sequenced by what
          depends on what, with {build.resource_count} pages we fetched and checked.
        </p>
      </div>
    )
  }

  const percent = Math.round(build.progress * 100)
  return (
    <div className="panel" aria-live="polite">
      <div className="flex items-baseline justify-between">
        <p className="text-sm font-medium text-ink-800">{build.stage}</p>
        <span className="text-xs tabular-nums text-ink-400">{Math.round(build.elapsed)}s</span>
      </div>
      {build.detail ? <p className="hint mt-1">{build.detail}</p> : null}

      <div
        className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-paper-300"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full bg-clay-500 transition-all duration-500 ease-out"
          style={{ width: `${Math.max(4, percent)}%` }}
        />
      </div>

      <p className="hint mt-3">
        We’re reading the subject’s prerequisites, then finding and checking real pages for each
        one. You only wait once — the next person asking gets it instantly.
      </p>
    </div>
  )
}
