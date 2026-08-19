"""Turning a computed `Plan` into stored rows, and stored rows back into JSON.

The planner is pure and knows nothing about persistence; this module is the
bridge. Its one structural rule is that **a path is never edited in place.**
Regenerating supersedes the current version and writes a new one, so every
earlier version stays retrievable and any two of them can be diffed. That is
what makes adaptation visible to a learner rather than an invisible database
write.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.core.mastery import MasteryTable
from app.core.planner import Plan, build_plan, preference_from
from app.core.retrieval import catalog_index
from app.core.skill_graph import load_graph
from app.models import Event, Learner, LearningPath, PathItem
from app.narration import narrate_all
from app.schemas import PathItemOut, PathResponse, ResourceOut

logger = logging.getLogger(__name__)


def goal_label(learner: Learner) -> str:
    """A human name for the goal, preferring the resolved node names."""
    graph = load_graph()
    names = [graph.require(g).name for g in learner.goal_node_ids if g in graph]
    return " and ".join(names) if names else (learner.goal_text or "your goal")


def plan_for(learner: Learner, mastery: MasteryTable, *, hours_per_week: float | None = None,
             cost_pref: str | None = None, format_pref: str | None = None) -> Plan:
    """Run the planner for one learner, optionally overriding a preference.

    Overrides exist for `/whatif`, which must recompute without touching the
    learner row or the database.
    """
    return build_plan(
        graph=load_graph(),
        mastery=mastery,
        goal_ids=list(learner.goal_node_ids),
        goal_label=goal_label(learner),
        prefs=preference_from(
            format_pref=format_pref or learner.format_pref,
            cost_pref=cost_pref or learner.cost_pref,
            language=learner.language,
            low_bandwidth=learner.low_bandwidth,
            experience_level=learner.experience_level,
        ),
        hours_per_week=hours_per_week if hours_per_week is not None else learner.hours_per_week,
        narrate=True,
    )


def latest_path(db: Session, learner_id: int, version: int | None = None) -> LearningPath | None:
    """The active version, or a specific historical one."""
    query = select(LearningPath).where(LearningPath.learner_id == learner_id)
    if version is not None:
        query = query.where(LearningPath.version == version)
    return db.exec(query.order_by(LearningPath.version.desc())).first()


def load_items(db: Session, path_id: int) -> list[PathItem]:
    return list(
        db.exec(
            select(PathItem).where(PathItem.path_id == path_id).order_by(PathItem.order_index)
        ).all()
    )


def persist_plan(
    db: Session, learner: Learner, plan: Plan, *, event_type: str, payload: dict | None = None
) -> tuple[LearningPath, bool]:
    """Write the plan as a new version and supersede the previous one."""
    # Milestones carry their own copy and no provenance record, so only resource
    # items are sent for narration.
    narratable = [i for i, item in enumerate(plan.items) if "why_needed" in item.provenance]
    narrated, degraded = narrate_all([plan.items[i].provenance for i in narratable])
    texts = [item.rationale_text for item in plan.items]
    for slot, text in zip(narratable, narrated, strict=True):
        texts[slot] = text

    previous = latest_path(db, learner.id)
    if previous is not None:
        previous.status = "superseded"
        db.add(previous)

    path = LearningPath(
        learner_id=learner.id,
        version=(previous.version + 1) if previous else 1,
        status="active",
        total_hours=plan.total_hours,
        finish_week=plan.finish_week,
        llm_degraded=degraded,
    )
    db.add(path)
    db.commit()
    db.refresh(path)

    for item, text in zip(plan.items, texts, strict=True):
        db.add(
            PathItem(
                path_id=path.id,
                order_index=item.order_index,
                week_number=item.week_number,
                skill_id=item.skill_id,
                course_id=item.course_id,
                kind=item.kind,
                status="pending",
                est_hours=item.est_hours,
                provenance=item.provenance,
                rationale_text=text,
                alternatives=item.alternatives,
            )
        )
    db.add(Event(
        learner_id=learner.id,
        type=event_type,
        payload={"version": path.version, "items": len(plan.items),
                 "finish_week": plan.finish_week, **(payload or {})},
    ))
    db.commit()
    logger.info("learner %s: path v%d with %d items", learner.id, path.version, len(plan.items))
    return path, degraded


def carry_over_status(db: Session, old_path: LearningPath | None, new_path: LearningPath) -> None:
    """Preserve completion of skills that survive into the new version.

    Without this, every replan would silently reset a learner's progress -- the
    single most damaging thing an adaptive system can do.
    """
    if old_path is None:
        return
    done = {
        item.skill_id
        for item in load_items(db, old_path.id)
        if item.status == "done"
    }
    if not done:
        return
    for item in load_items(db, new_path.id):
        if item.skill_id in done and item.kind == "resource":
            item.status = "done"
            db.add(item)
    db.commit()


def to_response(
    db: Session, learner: Learner, path: LearningPath | None, *, degraded: bool = False
) -> PathResponse:
    """Project stored rows onto the wire shape the frontend consumes."""
    graph = load_graph()
    catalog = catalog_index()
    goal_names = [graph.require(g).name for g in learner.goal_node_ids if g in graph]

    if path is None:
        return PathResponse(
            learner_id=learner.id, path_id=None, version=0, status="none",
            total_hours=0.0, finish_week=0, hours_per_week=learner.hours_per_week,
            goal_node_ids=list(learner.goal_node_ids), goal_names=goal_names,
            items=[], llm_degraded=degraded,
        )

    items: list[PathItemOut] = []
    for row in load_items(db, path.id):
        chosen = catalog.get(row.course_id) if row.course_id else None
        items.append(
            PathItemOut(
                id=row.id,
                order_index=row.order_index,
                week_number=row.week_number,
                skill_id=row.skill_id,
                skill_name=(
                    graph.require(row.skill_id).name if row.skill_id in graph else row.skill_id
                ),
                kind=row.kind,
                status=row.status,
                est_hours=row.est_hours,
                course=ResourceOut(**chosen.as_dict()) if chosen else None,
                alternatives=[
                    ResourceOut(**catalog[alt].as_dict())
                    for alt in row.alternatives
                    if alt in catalog
                ],
                provenance=row.provenance,
                rationale_text=row.rationale_text,
            )
        )

    return PathResponse(
        learner_id=learner.id,
        path_id=path.id,
        version=path.version,
        status=path.status,
        total_hours=path.total_hours,
        finish_week=path.finish_week,
        hours_per_week=learner.hours_per_week,
        goal_node_ids=list(learner.goal_node_ids),
        goal_names=goal_names,
        items=items,
        llm_degraded=degraded or path.llm_degraded,
    )
