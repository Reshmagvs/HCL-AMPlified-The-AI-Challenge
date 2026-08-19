"""Path generation, retrieval and what-if projection.

The router is deliberately thin. All the reasoning lives in `core.planner`,
which is pure, and all the persistence lives in `app.pathing`, so what remains
here is request validation and HTTP semantics. The adaptation half of the same
URL prefix lives in `routers/adaptation.py`.

`/whatif` is the one endpoint with an unusual contract: it runs the full planner
against an overridden `hours_per_week` and returns the recomputed finish week
**without writing anything**. The learner is asking a question, not making a
decision, and a slider that silently rewrote their plan on every drag would be
hostile.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db import get_session
from app.pathing import carry_over_status, latest_path, persist_plan, plan_for, to_response
from app.routers.diagnostic import load_learner, load_mastery
from app.schemas import PathResponse, WhatIfRequest, WhatIfResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/path", tags=["path"])


@router.post("/generate/{learner_id}", response_model=PathResponse)
def generate(learner_id: int, db: Session = Depends(get_session)) -> PathResponse:
    """Build a new version of the learner's path from their current state."""
    learner = load_learner(db, learner_id)
    mastery = load_mastery(db, learner_id)
    previous = latest_path(db, learner_id)

    plan = plan_for(learner, mastery)
    path, degraded = persist_plan(db, learner, plan, event_type="path_generated")
    carry_over_status(db, previous, path)

    if plan.unbound_skills:
        logger.info(
            "learner %s: %d skills had no catalog resource (%s)",
            learner_id, len(plan.unbound_skills), plan.unbound_skills[:5],
        )
    return to_response(db, learner, path, degraded=degraded)


@router.get("/{learner_id}", response_model=PathResponse)
def get_path(
    learner_id: int, version: int | None = None, db: Session = Depends(get_session)
) -> PathResponse:
    """The active path, or a specific historical version."""
    learner = load_learner(db, learner_id)
    path = latest_path(db, learner_id, version)
    if version is not None and path is None:
        raise HTTPException(status_code=404, detail=f"no version {version} for this learner")
    return to_response(db, learner, path)


@router.post("/whatif", response_model=WhatIfResponse)
def whatif(payload: WhatIfRequest, db: Session = Depends(get_session)) -> WhatIfResponse:
    """Recompute under a hypothetical capacity. Writes nothing, ever."""
    learner = load_learner(db, payload.learner_id)
    mastery = load_mastery(db, payload.learner_id)

    plan = plan_for(
        learner,
        mastery,
        hours_per_week=payload.hours_per_week,
        cost_pref=payload.cost_pref,
        format_pref=payload.format_pref,
    )
    db.rollback()  # belt and braces: nothing this request touched may persist

    return WhatIfResponse(
        hours_per_week=payload.hours_per_week,
        finish_week=plan.finish_week,
        total_hours=plan.total_hours,
        item_count=len(plan.items),
        weeks=plan.weeks(),
        persisted=False,
    )
