"""Background topic builds, with progress a learner can actually watch.

Building a curriculum for a new subject takes a couple of minutes on a CPU: the
local model writes a syllabus at about five tokens a second, and then every
skill needs a live search whose results are all fetched and checked. None of
that is avoidable on this hardware, and none of it should happen inside a
request that a browser, a proxy or an impatient learner will abandon.

So the build runs in a thread and the request returns immediately with a job to
watch. Three things make it behave like a product rather than a spinner:

**One build per topic, ever.** Jobs are keyed by the normalised goal, so ten
learners asking for organic chemistry at the same moment join one build instead
of starting ten. The second asker sees the first asker's progress.

**Progress is real.** Each stage reports what it is actually doing -- which
skill is being searched for, how many resources have been verified -- because
"designing your syllabus" followed by "searching for materials on Quantum Gates
(4 of 9)" is a wait a person will sit through, and an unlabelled two-minute
spinner is one they will not.

**Failure is a state, not an exception.** A job that fails records why, in
words meant for a learner, and the interface offers the nearest curated
alternative instead of a stack trace.

Jobs live in memory. They describe work in flight, not durable state -- the
result of a successful build is in ``core.store``, which is on disk and shared.
Losing the job table on restart loses nothing that matters.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.core import store

logger = logging.getLogger(__name__)

# How long a finished job stays visible before it is swept. Long enough for a
# browser that polls every second to see the terminal state many times over.
RETENTION_SECONDS = 900.0


@dataclass
class Job:
    """One topic build, in flight or finished."""

    key: str
    goal_text: str
    status: str = "queued"  # queued | running | done | failed
    stage: str = "Starting"
    detail: str = ""
    progress: float = 0.0
    topic: str = ""
    goal_skill_ids: list[str] = field(default_factory=list)
    skill_count: int = 0
    resource_count: int = 0
    error: str = ""
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stage": self.stage,
            "detail": self.detail,
            "progress": round(self.progress, 3),
            "topic": self.topic,
            "goal_skill_ids": list(self.goal_skill_ids),
            "skill_count": self.skill_count,
            "resource_count": self.resource_count,
            "error": self.error,
            "elapsed": round((self.finished_at or time.monotonic()) - self.started_at, 1),
        }


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _sweep() -> None:
    """Drop finished jobs that nothing is watching any more. Caller holds the lock."""
    now = time.monotonic()
    for key in [
        key
        for key, job in _jobs.items()
        if job.finished_at and now - job.finished_at > RETENTION_SECONDS
    ]:
        del _jobs[key]


def get(goal_text: str) -> Job | None:
    """The job for this goal, if one is running or recently finished."""
    with _lock:
        return _jobs.get(store.topic_key(goal_text))


def start(goal_text: str) -> Job:
    """Begin building this topic, or return the build already in progress.

    Deliberately not idempotent on *finished* jobs: a completed build is in the
    store, so callers should consult the cache before coming here.
    """
    key = store.topic_key(goal_text)
    with _lock:
        _sweep()
        existing = _jobs.get(key)
        if existing and existing.status in ("queued", "running"):
            return existing
        job = Job(key=key, goal_text=goal_text)
        _jobs[key] = job

    thread = threading.Thread(target=_run, args=(job,), name=f"expand:{key[:24]}", daemon=True)
    thread.start()
    return job


def _run(job: Job) -> None:
    """Execute one build, reporting progress as it goes."""
    from app.core import expansion

    def report(stage: str, detail: str, progress: float) -> None:
        job.stage, job.detail, job.progress = stage, detail, progress

    job.status = "running"
    try:
        result = expansion.expand(job.goal_text, progress=report)
    except Exception as exc:  # noqa: BLE001 -- a thread that dies silently is worse
        logger.exception("topic build failed for %r", job.goal_text[:60])
        job.status, job.error = "failed", str(exc)[:300]
        job.stage, job.detail, job.progress = "Failed", str(exc)[:200], 1.0
        job.finished_at = time.monotonic()
        return

    job.finished_at = time.monotonic()
    if not result.ok:
        job.status, job.error = "failed", result.reason
        job.stage, job.detail, job.progress = "Failed", result.reason, 1.0
        return

    # The learner now has a plan they can look at. Placement questions for the
    # new skills are written behind them, so the diagnostic has something to ask
    # by the time they reach it.
    from app.core import placement, store as topic_store

    record = topic_store.find_topic(job.goal_text) or {}
    placement.write_in_background(record.get("skill_ids", []))

    job.status = "done"
    job.topic = result.topic
    job.goal_skill_ids = result.goal_skill_ids
    job.skill_count = result.skill_count
    job.resource_count = result.resource_count
    job.stage = "Ready"
    job.detail = f"{result.skill_count} skills, {result.resource_count} verified resources"
    job.progress = 1.0


def clear() -> None:
    """Forget every job. Used by tests."""
    with _lock:
        _jobs.clear()
