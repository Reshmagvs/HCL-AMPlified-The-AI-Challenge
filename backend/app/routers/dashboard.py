"""Dashboard: progress, hours, mastery spread, milestones and what to do next.

Everything here is derived from rows that already exist -- the path, the mastery
table and the event log -- so the dashboard cannot disagree with the path screen.
The activity feed in particular is a direct read of `Event`, which is why nothing
in this system mutates silently: if it is not in the log, it did not happen.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.core.skill_graph import load_graph
from app.db import get_session
from app.models import Event, LearningPath, PathItem
from app.pathing import latest_path, load_items, to_response
from app.routers.diagnostic import load_learner, load_mastery
from app.schemas import DashboardResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

ACTIVITY_LIMIT = 20
NEXT_ACTION_COUNT = 3


def _mastery_radar(learner_id: int, goal_ids: list[str], db: Session) -> list[dict[str, Any]]:
    """Mean mastery per track across the skills this learner actually needs.

    Averaging over the *required* set rather than the whole graph keeps the radar
    honest: a cybersecurity learner should not look 0% on machine learning.
    """
    graph = load_graph()
    table = load_mastery(db, learner_id)
    required = graph.required_for([g for g in goal_ids if g in graph])

    totals: dict[str, list[float]] = defaultdict(list)
    for skill_id in required:
        totals[graph.require(skill_id).track].append(table.score(skill_id))

    return [
        {
            "track": track,
            "mastery": round(sum(scores) / len(scores), 3),
            "skills": len(scores),
            "mastered": sum(1 for s in scores if s >= 0.7),
        }
        for track, scores in sorted(totals.items())
        if scores
    ]


def _milestones(items: list[PathItem]) -> list[dict[str, Any]]:
    return [
        {
            "week": item.week_number,
            "track": item.provenance.get("track", ""),
            "label": item.provenance.get("skill_name", "checkpoint"),
            "status": item.status,
        }
        for item in items
        if item.kind == "milestone"
    ]


def _activity(db: Session, learner_id: int) -> list[dict[str, Any]]:
    events = db.exec(
        select(Event)
        .where(Event.learner_id == learner_id)
        .order_by(Event.id.desc())
        .limit(ACTIVITY_LIMIT)
    ).all()
    return [
        {
            "id": event.id,
            "type": event.type,
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        }
        for event in events
    ]


def _current_week(items: list[PathItem]) -> int:
    """The week of the earliest unfinished item -- where the learner stands now."""
    pending = [i.week_number for i in items if i.status != "done"]
    return min(pending) if pending else max((i.week_number for i in items), default=0)


@router.get("/{learner_id}", response_model=DashboardResponse)
def dashboard(learner_id: int, db: Session = Depends(get_session)) -> DashboardResponse:
    """One screen's worth of state, assembled from existing rows only."""
    learner = load_learner(db, learner_id)
    graph = load_graph()
    path: LearningPath | None = latest_path(db, learner_id)
    items = load_items(db, path.id) if path else []

    done = [i for i in items if i.status == "done"]
    hours_done = round(sum(i.est_hours for i in done), 2)
    hours_total = round(sum(i.est_hours for i in items), 2)

    response = to_response(db, learner, path)
    next_actions = [
        out
        for out in response.items
        if out.status != "done"
    ][:NEXT_ACTION_COUNT]

    return DashboardResponse(
        learner_id=learner_id,
        goal_names=[graph.require(g).name for g in learner.goal_node_ids if g in graph],
        items_total=len(items),
        items_done=len(done),
        progress_pct=round(100.0 * len(done) / len(items), 1) if items else 0.0,
        hours_done=hours_done,
        hours_remaining=round(max(0.0, hours_total - hours_done), 2),
        finish_week=path.finish_week if path else 0,
        current_week=_current_week(items),
        mastery_radar=_mastery_radar(learner_id, list(learner.goal_node_ids), db),
        milestones=_milestones(items),
        next_actions=next_actions,
        activity=_activity(db, learner_id),
    )
