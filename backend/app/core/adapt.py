"""Event-driven replanning, and the diff that makes it visible.

Adaptation that a learner cannot see is indistinguishable from adaptation that
did not happen. So every event here follows the same shape:

1. the event is appended to the audit log (the router does this),
2. its effect is applied to mastery or to the learner's constraints,
3. the path is regenerated as a **new version** -- never mutated,
4. the two versions are compared and the difference is returned.

Step 3 is what makes step 4 possible, and step 4 is what turns "the system
adapted" into "+2 items, -1 item, finish moved from week 12 to 13".

This module is pure: it decides *what changed about the learner*, and returns
the rows the caller should save. It never touches a session, and it never calls
a model, so all seven event behaviours are unit-testable in isolation.

| event               | effect                                                  |
|---------------------|---------------------------------------------------------|
| `milestone_failed`  | drop mastery below threshold; remediation returns to the path |
| `too_easy`          | raise mastery to 0.8; the skill leaves the gap and the schedule pulls forward |
| `too_hard`          | demote the under-weighted prerequisites so they are taught first |
| `behind_schedule`   | repack, and offer explicit scope-reduction options      |
| `goal_changed`      | re-resolve the goal, preserving mastery for overlapping work |
| `resource_disliked` | rebind to the rank-2 resource for the same skill        |
| `completed_item`    | mark done, raise mastery, advance progress              |
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.mastery import MASTERY_THRESHOLD, MasteryTable
from app.core.skill_graph import load_graph

logger = logging.getLogger(__name__)

TOO_EASY_SCORE = 0.8
COMPLETED_SCORE = 0.85
MILESTONE_FAIL_SCORE = 0.25
TOO_HARD_PREREQ_SCORE = 0.1


@dataclass
class EventOutcome:
    """What the router should persist, and what to tell the learner."""

    message: str
    replan: bool = True
    options: list[str] = field(default_factory=list)
    touched_items: list[Any] = field(default_factory=list)
    learner_changed: bool = False
    log: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Snapshots and diffs
# --------------------------------------------------------------------------- #
def snapshot_of(items: list[Any], finish_week: int) -> dict[str, Any]:
    """A comparable summary of one path version.

    Keyed by `(skill_id, kind)` rather than row id, because a regenerated path
    has entirely new rows for what is conceptually the same step.
    """
    return {
        "finish_week": finish_week,
        "items": {
            f"{item.skill_id}|{item.kind}": {
                "skill_id": item.skill_id,
                "kind": item.kind,
                "week": item.week_number,
                "course_id": item.course_id,
                "order_index": item.order_index,
            }
            for item in items
        },
    }


def compute_diff(before: dict[str, Any], after: dict[str, Any], v1: int, v2: int) -> dict[str, Any]:
    """Set difference over two snapshots, split by what kind of change it is."""
    old_items: dict[str, Any] = before["items"]
    new_items: dict[str, Any] = after["items"]

    added = [new_items[k] for k in sorted(set(new_items) - set(old_items))]
    removed = [old_items[k] for k in sorted(set(old_items) - set(new_items))]

    moved: list[dict[str, Any]] = []
    swapped: list[dict[str, Any]] = []
    for key in sorted(set(old_items) & set(new_items)):
        old, new = old_items[key], new_items[key]
        if old["week"] != new["week"]:
            moved.append({
                "skill_id": new["skill_id"], "from_week": old["week"], "to_week": new["week"]
            })
        if old["course_id"] != new["course_id"]:
            swapped.append({
                "skill_id": new["skill_id"],
                "from_course_id": old["course_id"],
                "to_course_id": new["course_id"],
            })

    delta = after["finish_week"] - before["finish_week"]
    return {
        "from_version": v1,
        "to_version": v2,
        "added": added,
        "removed": removed,
        "moved_weeks": moved,
        "resource_swapped": swapped,
        "finish_week_delta": delta,
        "unchanged": not (added or removed or moved or swapped or delta),
    }


# --------------------------------------------------------------------------- #
# Event handlers
# --------------------------------------------------------------------------- #
def _find_item(items: list[Any], payload: dict[str, Any]) -> Any | None:
    """Locate the item an event refers to, by row id or by skill id."""
    if (item_id := payload.get("item_id")) is not None:
        return next((i for i in items if i.id == item_id), None)
    if skill_id := payload.get("skill_id"):
        return next((i for i in items if i.skill_id == skill_id), None)
    return None


def _require_item(items: list[Any], payload: dict[str, Any], event: str) -> Any:
    item = _find_item(items, payload)
    if item is None:
        raise ValueError(f"{event} needs an item_id or skill_id that exists in the current path")
    return item


def _too_easy(mastery: MasteryTable, items: list[Any], payload: dict[str, Any]) -> EventOutcome:
    item = _require_item(items, payload, "too_easy")
    mastery.set(item.skill_id, TOO_EASY_SCORE, "milestone", confidence=0.7, force=True)
    return EventOutcome(
        message=(
            f"Marked as already known. {item.skill_id} leaves the path and everything "
            "after it moves earlier."
        ),
        log={"skill_id": item.skill_id, "new_score": TOO_EASY_SCORE},
    )


def _too_hard(mastery: MasteryTable, items: list[Any], payload: dict[str, Any]) -> EventOutcome:
    """Insert the prerequisites the diagnostic under-weighted, ahead of this item."""
    item = _require_item(items, payload, "too_hard")
    graph = load_graph()
    prereqs = list(graph.require(item.skill_id).prerequisites) if item.skill_id in graph else []
    demoted = [p for p in prereqs if mastery.score(p) >= MASTERY_THRESHOLD]

    for prereq in demoted:
        mastery.set(prereq, TOO_HARD_PREREQ_SCORE, "diagnostic", confidence=0.5, force=True)
    if not demoted:
        # Everything upstream is already in the gap, so it is already scheduled first.
        mastery.set(item.skill_id, 0.0, "diagnostic", confidence=0.3, force=True)

    return EventOutcome(
        message=(
            f"Added {len(demoted)} prerequisite(s) before {item.skill_id}."
            if demoted
            else f"{item.skill_id} stays, with its prerequisites already scheduled first."
        ),
        log={"skill_id": item.skill_id, "reinstated": demoted},
    )


def _milestone_failed(mastery: MasteryTable, items: list[Any], payload: dict[str, Any]) -> EventOutcome:
    """A failed checkpoint drops everything it covered back below threshold.

    A track checkpoint tests the whole block behind it, so failing it is evidence
    about that block -- not about the single skill the milestone row happens to
    point at. Demoting only that one skill left the path unchanged whenever the
    skill was already in the gap, which made the event look like it had done
    nothing.
    """
    item = _require_item(items, payload, "milestone_failed")
    skills = list(payload.get("skill_ids") or [])
    covered: list[Any] = []

    if not skills and item.kind == "milestone":
        track = (item.provenance.get("milestone") or {}).get("track")
        covered = [
            other
            for other in items
            if other.kind == "resource"
            and other.order_index <= item.order_index
            and other.provenance.get("track") == track
            and mastery.is_mastered(other.skill_id)
        ]
        skills = sorted({other.skill_id for other in covered})
    if not skills:
        skills = [item.skill_id]

    for skill_id in skills:
        mastery.set(skill_id, MILESTONE_FAIL_SCORE, "milestone", confidence=1.0, force=True)

    # Re-open the steps the checkpoint covered. Without this, `carry_over_status`
    # would faithfully copy their "done" flag into the regenerated path and the
    # learner would see remediation they are already marked as having finished.
    for other in covered:
        other.status = "pending"

    return EventOutcome(
        message=f"Checkpoint failed. Remediation for {len(skills)} skill(s) returns to the path.",
        touched_items=covered,
        log={"skills": list(skills), "new_score": MILESTONE_FAIL_SCORE},
    )


def _completed_item(mastery: MasteryTable, items: list[Any], payload: dict[str, Any]) -> EventOutcome:
    item = _require_item(items, payload, "completed_item")
    item.status = "done"
    mastery.set(item.skill_id, COMPLETED_SCORE, "milestone", confidence=0.8, force=True)
    return EventOutcome(
        message=f"{item.skill_id} marked complete.",
        replan=False,
        touched_items=[item],
        log={"skill_id": item.skill_id},
    )


def _resource_disliked(items: list[Any], payload: dict[str, Any]) -> EventOutcome:
    """Rebind to the next-best resource for the same skill, without replanning."""
    item = _require_item(items, payload, "resource_disliked")
    alternatives = [a for a in (item.alternatives or []) if a != item.course_id]
    if not alternatives:
        return EventOutcome(
            message=f"No alternative resource is available for {item.skill_id}.",
            replan=False,
        )

    previous, item.course_id = item.course_id, alternatives[0]
    item.alternatives = [*alternatives[1:], previous] if previous else alternatives[1:]
    item.provenance = {
        **item.provenance,
        "why_this_resource": {
            **item.provenance.get("why_this_resource", {}),
            "resource_id": item.course_id,
            "rebound_from": previous,
            "reasons": ["you asked for a different resource", "next-best score for this skill"],
        },
    }
    item.rationale_text = (
        f"Swapped to the next-best resource for {item.skill_id} after you dismissed the previous one."
    )
    return EventOutcome(
        message=f"Swapped the resource for {item.skill_id}.",
        replan=False,
        touched_items=[item],
        log={"skill_id": item.skill_id, "from": previous, "to": item.course_id},
    )


def _behind_schedule(learner: Any, payload: dict[str, Any]) -> EventOutcome:
    """Repack at the learner's real capacity and offer explicit scope cuts."""
    weeks_behind = int(payload.get("weeks_behind", 2))
    new_hours = payload.get("hours_per_week")
    changed = False
    if new_hours:
        learner.hours_per_week = max(1.0, float(new_hours))
        changed = True

    return EventOutcome(
        message=(
            f"Repacked the schedule around being {weeks_behind} week(s) behind."
            + (f" Capacity is now {learner.hours_per_week:g}h/week." if changed else "")
        ),
        options=[
            "Increase hours per week (try the what-if slider first)",
            "Drop to a narrower goal, keeping the shared foundations",
            "Accept a later finish week and keep the full scope",
            "Skip optional depth: keep only skills that unlock later work",
        ],
        learner_changed=changed,
        log={"weeks_behind": weeks_behind, "hours_per_week": learner.hours_per_week},
    )


def _goal_changed(learner: Any, mastery: MasteryTable, payload: dict[str, Any]) -> EventOutcome:
    """Re-resolve the goal while keeping everything already demonstrated.

    Mastery is stored per skill, not per path, so overlapping completed work is
    preserved automatically -- the point of this handler is to make that explicit
    and to report how much of the old work still counts.
    """
    graph = load_graph()
    new_ids = [g for g in (payload.get("goal_node_ids") or []) if g in graph]
    goal_text = (payload.get("goal_text") or "").strip()

    if not new_ids and goal_text:
        from app.resolution import resolve_goal

        new_ids, _candidates, _degraded = resolve_goal(goal_text)
    if not new_ids:
        raise ValueError("goal_changed needs goal_node_ids or goal_text")

    previous_required = graph.required_for([g for g in learner.goal_node_ids if g in graph])
    new_required = graph.required_for(new_ids)
    preserved = sorted(
        s for s in previous_required & new_required if mastery.is_mastered(s)
    )

    learner.goal_node_ids = new_ids
    if goal_text:
        learner.goal_text = goal_text

    return EventOutcome(
        message=(
            f"Goal updated to {', '.join(graph.require(g).name for g in new_ids)}. "
            f"{len(preserved)} skill(s) you already know carry over."
        ),
        learner_changed=True,
        log={"goal_node_ids": new_ids, "preserved": preserved},
    )


HANDLERS = {
    "too_easy", "too_hard", "milestone_failed", "completed_item",
    "resource_disliked", "behind_schedule", "goal_changed",
}


def apply_event(
    *,
    event_type: str,
    payload: dict[str, Any],
    learner: Any,
    mastery: MasteryTable,
    items: list[Any],
) -> EventOutcome:
    """Dispatch one event to its handler. Unknown types are a caller error."""
    if event_type not in HANDLERS:
        raise ValueError(f"unknown event type {event_type!r}")

    if event_type == "too_easy":
        return _too_easy(mastery, items, payload)
    if event_type == "too_hard":
        return _too_hard(mastery, items, payload)
    if event_type == "milestone_failed":
        return _milestone_failed(mastery, items, payload)
    if event_type == "completed_item":
        return _completed_item(mastery, items, payload)
    if event_type == "resource_disliked":
        return _resource_disliked(items, payload)
    if event_type == "behind_schedule":
        return _behind_schedule(learner, payload)
    return _goal_changed(learner, mastery, payload)
