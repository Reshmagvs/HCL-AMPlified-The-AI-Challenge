"""The planner: required -> gap -> order -> bind -> pack.

This is the product. Everything else supplies inputs to it or renders its
output.

**required** ``ancestors_closure(goals) | goals``. Pure reverse reachability.

**gap** ``{n in required : mastery(n) < 0.7}``. Unmeasured counts as zero, and a
self-report is capped at 0.4, so nothing leaves the path on a claim alone.

**order** A topological sort of the gap. This is the step a similarity-ranked
list cannot produce: it answers "what am I allowed to start now", not "what is
relevant". Ties break deterministically (see ``skill_graph.topological_sort``),
so identical input yields a byte-identical path.

**bind** For each skill, hard filters first, then the weighted score. Rank 1 is
bound; ranks 2-3 are the swap options and the "chosen over N alternatives" line.

**pack** Greedy first-fit into weeks at the learner's real capacity. Two
invariants hold by construction: no week is allocated more than
``hours_per_week``, and no skill is placed before any prerequisite's week. A
resource longer than one week's capacity spills into the following week rather
than being crammed in. A 3-question milestone check is inserted at each track
boundary.

The planner is pure. It receives an already-resolved graph, mastery table and
preferences, touches no database, calls no model, and returns a value. That is
what makes the correctness properties testable at all.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from app.core.explain import build_provenance, render_template
from app.core.mastery import MasteryTable
from app.core.retrieval import Preferences, ScoredResource, expected_level, score_resources
from app.core.skill_graph import SkillGraph

logger = logging.getLogger(__name__)

MILESTONE_HOURS = 0.5
MAX_PLAN_WEEKS = 520  # a decade; a guard against a pathological capacity, not a policy


@dataclass
class PlannedItem:
    """One step in a plan, before it is persisted."""

    order_index: int
    skill_id: str
    kind: str = "resource"  # resource | milestone
    week_number: int = 1
    course_id: str | None = None
    est_hours: float = 0.0
    alternatives: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    rationale_text: str = ""


@dataclass
class Plan:
    """The complete result of one planning run."""

    items: list[PlannedItem]
    goal_ids: list[str]
    hours_per_week: float
    total_hours: float = 0.0
    finish_week: int = 0
    week_load: dict[int, float] = field(default_factory=dict)
    unbound_skills: list[str] = field(default_factory=list)

    @property
    def skill_ids(self) -> list[str]:
        return [i.skill_id for i in self.items if i.kind == "resource"]

    def weeks(self) -> list[dict[str, Any]]:
        """Per-week summary used by the timeline and the what-if response."""
        by_week: dict[int, list[PlannedItem]] = {}
        for item in self.items:
            by_week.setdefault(item.week_number, []).append(item)
        return [
            {
                "week": week,
                "allocated_hours": round(self.week_load.get(week, 0.0), 2),
                "item_count": len(items),
                "skills": [i.skill_id for i in items],
            }
            for week, items in sorted(by_week.items())
        ]


# --------------------------------------------------------------------------- #
# Step 4: resource binding
# --------------------------------------------------------------------------- #
def bind_resources(
    graph: SkillGraph, order: list[str], prefs: Preferences
) -> dict[str, list[ScoredResource]]:
    """Rank up to three catalog resources per skill. Empty list means unbound."""
    return {
        skill_id: score_resources(
            skill_id, graph.require(skill_id).difficulty, prefs, top_k=3
        )
        for skill_id in order
    }


# --------------------------------------------------------------------------- #
# Step 5: weekly packing
# --------------------------------------------------------------------------- #
class WeekPacker:
    """Greedy first-fit calendar with a per-week capacity ledger.

    An item whose duration exceeds the remaining capacity of its earliest legal
    week spills into following weeks. The ledger is what the "no week exceeds
    hours_per_week" invariant is checked against: an item's ``week_number`` is
    where it *starts*, while its hours are charged across the weeks it actually
    occupies.
    """

    def __init__(self, hours_per_week: float) -> None:
        self.capacity = max(0.5, float(hours_per_week))
        self.load: dict[int, float] = {}

    def remaining(self, week: int) -> float:
        return self.capacity - self.load.get(week, 0.0)

    def place(self, hours: float, earliest_week: int) -> int:
        """Charge ``hours`` from the first week at or after ``earliest_week``."""
        week = max(1, earliest_week)
        while self.remaining(week) <= 1e-9 and week < MAX_PLAN_WEEKS:
            week += 1

        start = week
        outstanding = max(0.0, hours)
        if outstanding == 0.0:
            self.load.setdefault(start, 0.0)
            return start

        while outstanding > 1e-9 and week < MAX_PLAN_WEEKS:
            take = min(outstanding, self.remaining(week))
            self.load[week] = self.load.get(week, 0.0) + take
            outstanding -= take
            if outstanding > 1e-9:
                week += 1
        return start

    def spans(self, hours: float) -> int:
        """How many weeks an item of this size needs at full capacity."""
        return max(1, math.ceil(hours / self.capacity - 1e-9))


# The least a step may be scheduled for, however short its resource.
MIN_STEP_HOURS = 0.5

# A resource shorter than this fraction of the skill's own estimate is treated as
# insufficient on its own rather than as evidence the skill is quick.
SHORT_RESOURCE_FLOOR = 0.5


def scheduled_hours(node, chosen: ScoredResource | None) -> float:
    """How much of the learner's week this step actually costs.

    A resource's full length is the wrong number to schedule. CS50 is twenty
    hours and covers six skills; Khan Academy's Algebra 2 is sixty hours and
    covers two. Charging the whole course to one node produced a sixty-hour week
    inside a six-hour budget and pushed the finish week past seventy.

    So the resource's length is divided across the skills it covers, and the
    result is bounded *both ways* by the estimate for this node -- which is the
    more trustworthy figure for "how long does this skill take". The card still
    shows the resource's real duration; only the scheduling arithmetic uses this.

    The lower bound matters as much as the upper one, and only became visible
    once resources were discovered rather than curated. A curated course runs for
    hours; a Wikipedia article is a twenty-minute read. Scheduling the read as
    the whole cost of the skill produced a nine-skill quantum computing plan that
    finished in week one -- arithmetically consistent and pedagogically absurd.
    A resource shorter than the skill needs means the resource is not sufficient
    on its own, not that the skill got easier.
    """
    if chosen is None:
        return node.est_hours
    resource = chosen.resource
    share = resource.duration_hours / max(1, len(resource.skills_covered))
    floor = max(MIN_STEP_HOURS, node.est_hours * SHORT_RESOURCE_FLOOR)
    return round(max(floor, min(share, node.est_hours * 2.0)), 2)


def _earliest_week(
    graph: SkillGraph, skill_id: str, placed: dict[str, int], packer: WeekPacker,
    finish: dict[str, int],
) -> int:
    """No skill may start before every prerequisite in the plan has finished."""
    weeks = [finish[p] for p in graph.require(skill_id).prerequisites if p in placed]
    del packer  # capacity is handled by the packer itself; this is the ordering floor
    return max(weeks) if weeks else 1


# --------------------------------------------------------------------------- #
# The planner
# --------------------------------------------------------------------------- #
def build_plan(
    *,
    graph: SkillGraph,
    mastery: MasteryTable,
    goal_ids: list[str],
    goal_label: str,
    prefs: Preferences,
    hours_per_week: float,
    narrate: bool = False,
) -> Plan:
    """Run the five steps and return a complete, ordered, scheduled plan."""
    goals = [g for g in dict.fromkeys(goal_ids) if g in graph]
    if not goals:
        logger.warning("no resolvable goal nodes in %s", goal_ids)
        return Plan(items=[], goal_ids=[], hours_per_week=hours_per_week)

    required = graph.required_for(goals)
    gap = mastery.gap(required)
    order = graph.topological_sort(gap)
    ranked = bind_resources(graph, order, prefs)

    packer = WeekPacker(hours_per_week)
    items: list[PlannedItem] = []
    start_week: dict[str, int] = {}
    finish_week: dict[str, int] = {}
    unbound: list[str] = []
    index = 0

    for skill_id in order:
        node = graph.require(skill_id)
        candidates = ranked.get(skill_id, [])
        chosen = candidates[0] if candidates else None
        if chosen is None:
            unbound.append(skill_id)

        hours = scheduled_hours(node, chosen)
        floor = _earliest_week(graph, skill_id, start_week, packer, finish_week)
        week = packer.place(hours, floor)
        start_week[skill_id] = week
        finish_week[skill_id] = week + packer.spans(hours) - 1

        value = mastery.get(skill_id)
        provenance = build_provenance(
            graph=graph,
            skill_id=skill_id,
            goal_ids=goals,
            goal_label=goal_label,
            mastery_score=value.score,
            mastery_source=value.source,
            evidence_q_ids=list(value.evidence_q_ids),
            ranked=candidates,
            week=week,
            scheduled_hours=round(hours, 2),
            prefs=prefs,
            hours_per_week=hours_per_week,
        )
        items.append(
            PlannedItem(
                order_index=index,
                skill_id=skill_id,
                kind="resource",
                week_number=week,
                course_id=chosen.resource.id if chosen else None,
                est_hours=round(hours, 2),
                alternatives=[c.resource.id for c in candidates[1:]],
                provenance=provenance,
                rationale_text="" if narrate else render_template(provenance),
            )
        )
        index += 1

    items = _insert_milestones(items, packer)

    plan = Plan(
        items=items,
        goal_ids=goals,
        hours_per_week=hours_per_week,
        total_hours=round(sum(i.est_hours for i in items), 2),
        finish_week=max((i.week_number for i in items), default=0),
        week_load={w: round(h, 4) for w, h in sorted(packer.load.items())},
        unbound_skills=unbound,
    )
    plan.finish_week = max([*plan.week_load, plan.finish_week], default=0)
    return plan


def _insert_milestones(items: list[PlannedItem], packer: WeekPacker) -> list[PlannedItem]:
    """Add one 3-question checkpoint per track, after that track's final skill.

    A checkpoint fires at a *track boundary*, not at every switch between tracks.
    A topological order interleaves foundations with domain work constantly, so
    inserting on each switch produced seventeen quizzes in one path -- the
    boundary that matters is the point after which no further work in that track
    remains.
    """
    if not items:
        return items

    last_of_track: dict[str, PlannedItem] = {}
    for item in items:
        track = item.provenance.get("track", "")
        if track:
            last_of_track[track] = item

    merged: list[PlannedItem] = []
    index = 0
    for item in items:
        item.order_index = index
        merged.append(item)
        index += 1

        track = item.provenance.get("track", "")
        if last_of_track.get(track) is not item:
            continue
        week = packer.place(MILESTONE_HOURS, item.week_number)
        merged.append(
            PlannedItem(
                order_index=index,
                skill_id=item.skill_id,
                kind="milestone",
                week_number=week,
                course_id=None,
                est_hours=MILESTONE_HOURS,
                provenance={
                    "skill": item.skill_id,
                    "skill_name": f"{track} checkpoint",
                    "track": track,
                    "milestone": {"track": track, "questions": 3, "covers_up_to": item.skill_id},
                },
                rationale_text=(
                    f"A three-question checkpoint closes out the {track} block, so a gap "
                    f"here is caught before the next track builds on it."
                ),
            )
        )
        index += 1
    return merged


def week_allocations(
    items: list[tuple[int, float]], hours_per_week: float
) -> dict[int, float]:
    """Replay the packing ledger from stored items: week -> hours actually spent.

    An item's ``week_number`` is where it *starts*; a resource longer than one
    week of capacity spills into the weeks after it. Summing item hours by start
    week therefore reports "20 hours" in a 6-hour week, which reads as a bug even
    though the schedule is correct. This gives the interface the real figure.

    Takes ``(week_number, est_hours)`` pairs so it can be used on planner output
    and on database rows alike.
    """
    capacity = max(0.5, float(hours_per_week))
    load: dict[int, float] = {}

    for start_week, hours in sorted(items):
        week = max(1, start_week)
        outstanding = max(0.0, hours)
        load.setdefault(week, 0.0)
        while outstanding > 1e-9 and week < MAX_PLAN_WEEKS:
            take = min(outstanding, max(0.0, capacity - load.get(week, 0.0)))
            if take <= 1e-9:
                week += 1
                load.setdefault(week, 0.0)
                continue
            load[week] = load.get(week, 0.0) + take
            outstanding -= take
            if outstanding > 1e-9:
                week += 1
    return {week: round(hours, 2) for week, hours in sorted(load.items())}


def preference_from(
    *,
    format_pref: str,
    cost_pref: str,
    language: str,
    low_bandwidth: bool,
    experience_level: str,
) -> Preferences:
    """Adapter so routers never construct ``Preferences`` field by field."""
    return Preferences(
        format_pref=format_pref or "any",
        cost_pref=cost_pref or "any",
        language=language or "en",
        low_bandwidth=bool(low_bandwidth),
        experience_level=experience_level or "beginner",
    )


def level_for(difficulty: int) -> str:
    """Re-exported so the routers do not import retrieval directly."""
    return expected_level(difficulty)
