"""Adaptation endpoints: events in, a new path version and a diff out.

These share the `/api/path` prefix with the generation router but are kept in a
separate module because they answer a different question. Generation asks "what
should this learner do"; adaptation asks "what changed, and can the learner see
it". The second is what makes replanning believable rather than an invisible
database write.

Every event follows the same four steps: append to the audit log, apply the
effect to mastery or to the learner, regenerate as a **new version**, and return
the difference between the two versions.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.core.adapt import apply_event, compute_diff, snapshot_of
from app.db import get_session
from app.models import Event
from app.pathing import carry_over_status, latest_path, load_items, persist_plan, plan_for
from app.routers.diagnostic import load_learner, load_mastery, persist_mastery
from app.schemas import PathDiff, PathEventRequest, PathEventResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/path", tags=["path"])


@router.post("/event", response_model=PathEventResponse)
def path_event(payload: PathEventRequest, db: Session = Depends(get_session)) -> PathEventResponse:
    """Record an event, apply its effect, replan, and return the diff."""
    learner = load_learner(db, payload.learner_id)
    current = latest_path(db, payload.learner_id)
    if current is None:
        raise HTTPException(status_code=409, detail="generate a path before sending events")

    mastery = load_mastery(db, payload.learner_id)
    before = snapshot_of(load_items(db, current.id), current.finish_week)

    db.add(Event(learner_id=learner.id, type=payload.type, payload=payload.payload))
    db.commit()

    outcome = apply_event(
        event_type=payload.type,
        payload=payload.payload,
        learner=learner,
        mastery=mastery,
        items=load_items(db, current.id),
    )
    persist_mastery(db, learner.id, mastery)
    for row in outcome.touched_items:
        db.add(row)
    if outcome.learner_changed:
        db.add(learner)
    db.commit()
    db.refresh(learner)

    if not outcome.replan:
        return PathEventResponse(
            event=payload.type, message=outcome.message, version=current.version,
            diff=PathDiff(from_version=current.version, to_version=current.version),
            options=outcome.options,
        )

    plan = plan_for(learner, mastery)
    new_path, degraded = persist_plan(
        db, learner, plan, event_type=f"replanned:{payload.type}", payload=outcome.log
    )
    carry_over_status(db, current, new_path)

    after = snapshot_of(load_items(db, new_path.id), new_path.finish_week)
    diff = compute_diff(before, after, current.version, new_path.version)

    return PathEventResponse(
        event=payload.type,
        message=outcome.message,
        version=new_path.version,
        diff=PathDiff(**diff),
        options=outcome.options,
        llm_degraded=degraded,
    )


@router.get("/{learner_id}/diff/{from_version}/{to_version}", response_model=PathDiff)
def path_diff(
    learner_id: int, from_version: int, to_version: int, db: Session = Depends(get_session)
) -> PathDiff:
    """What changed between any two versions of this learner's path."""
    load_learner(db, learner_id)
    older = latest_path(db, learner_id, from_version)
    newer = latest_path(db, learner_id, to_version)
    if older is None or newer is None:
        missing = from_version if older is None else to_version
        raise HTTPException(status_code=404, detail=f"no version {missing} for this learner")

    before = snapshot_of(load_items(db, older.id), older.finish_week)
    after = snapshot_of(load_items(db, newer.id), newer.finish_week)
    return PathDiff(**compute_diff(before, after, from_version, to_version))
