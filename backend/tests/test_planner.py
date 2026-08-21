"""Phase 6 acceptance: the flagship correctness properties of the planner.

These are the tests the rest of the product stands on. If a returned ordering
can be violated, or if the same input produces two different paths, nothing
downstream -- provenance, adaptation, diffs, the dashboard -- means anything.

They are all checked against the *real* graph and the *real* catalog, over many
randomly chosen goals, rather than on a single curated example.
"""

from __future__ import annotations

import json
import random

import pytest

from app.core.mastery import MasteryTable
from app.core.planner import build_plan
from app.core.retrieval import Preferences, catalog_index

SEED = 20260819


def plan_for_goals(graph, goals, *, hours=6.0, prefs=None, mastery=None):
    return build_plan(
        graph=graph,
        mastery=mastery or MasteryTable(),
        goal_ids=list(goals),
        goal_label="test goal",
        prefs=prefs or Preferences(),
        hours_per_week=hours,
    )


def random_goals(graph, count: int, rng: random.Random) -> list[list[str]]:
    """Prefer terminal nodes -- those are what a learner actually aims at."""
    leaves = sorted(n for n in graph.nodes if not graph.children.get(n))
    return [rng.sample(leaves, rng.randint(1, 2)) for _ in range(count)]


# --------------------------------------------------------------------------- #
# The flagship property
# --------------------------------------------------------------------------- #
def test_zero_prerequisite_violations_across_100_paths(graph) -> None:
    rng = random.Random(SEED)
    violations: list[str] = []
    checked = 0

    for goals in random_goals(graph, 100, rng):
        plan = plan_for_goals(graph, goals, hours=rng.choice([2.0, 6.0, 12.0, 20.0]))
        order = {item.skill_id: item.order_index for item in plan.items if item.kind == "resource"}
        week = {item.skill_id: item.week_number for item in plan.items if item.kind == "resource"}
        checked += 1

        for skill_id in order:
            for prereq in graph.require(skill_id).prerequisites:
                if prereq in order and order[prereq] > order[skill_id]:
                    violations.append(f"{goals}: {prereq} ordered after {skill_id}")
                if prereq in week and week[prereq] > week[skill_id]:
                    violations.append(f"{goals}: {prereq} scheduled after {skill_id}")

    assert checked == 100
    assert violations == [], f"{len(violations)} violations, first five: {violations[:5]}"


def test_output_is_byte_identical_across_ten_runs(graph) -> None:
    goals = ["ml.engineer"]
    reference = None
    for _ in range(10):
        plan = plan_for_goals(graph, goals)
        serialised = json.dumps(
            [
                [i.order_index, i.week_number, i.skill_id, i.course_id, i.kind,
                 i.est_hours, i.alternatives, i.provenance, i.rationale_text]
                for i in plan.items
            ],
            sort_keys=True,
        )
        if reference is None:
            reference = serialised
        assert serialised == reference, "identical input must produce an identical path"


# --------------------------------------------------------------------------- #
# Scheduling invariants
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("hours", [1.0, 2.0, 6.0, 15.0, 40.0])
def test_no_week_is_allocated_more_than_capacity(graph, hours) -> None:
    plan = plan_for_goals(graph, ["ml.engineer"], hours=hours)
    for week, allocated in plan.week_load.items():
        assert allocated <= hours + 1e-6, f"week {week} allocated {allocated}h of a {hours}h budget"


def test_finish_week_equals_the_last_scheduled_week(graph) -> None:
    plan = plan_for_goals(graph, ["web.fullstack_engineer"], hours=8.0)
    assert plan.finish_week == max(i.week_number for i in plan.items)
    assert plan.finish_week == max(plan.week_load)


def test_no_skill_appears_twice(graph) -> None:
    rng = random.Random(SEED + 1)
    for goals in random_goals(graph, 25, rng):
        skills = [i.skill_id for i in plan_for_goals(graph, goals).items if i.kind == "resource"]
        assert len(skills) == len(set(skills)), f"duplicate skill for goals {goals}"


def test_one_hour_per_week_produces_a_valid_very_long_path(graph) -> None:
    fast = plan_for_goals(graph, ["ml.engineer"], hours=40.0)
    slow = plan_for_goals(graph, ["ml.engineer"], hours=1.0)

    assert slow.items, "a tiny budget still produces a path"
    assert slow.finish_week > fast.finish_week * 5
    assert all(load <= 1.0 + 1e-6 for load in slow.week_load.values())


def test_more_hours_never_pushes_the_finish_week_later(graph) -> None:
    weeks = [plan_for_goals(graph, ["da.analyst"], hours=h).finish_week for h in (2, 6, 12, 24)]
    assert weeks == sorted(weeks, reverse=True), f"finish weeks should shrink: {weeks}"


# --------------------------------------------------------------------------- #
# Binding
# --------------------------------------------------------------------------- #
def test_every_bound_course_exists_in_the_catalog(graph) -> None:
    """50 generations, zero references to a resource that is not in the file."""
    catalog = catalog_index()
    rng = random.Random(SEED + 2)
    bound = 0

    for goals in random_goals(graph, 50, rng):
        for item in plan_for_goals(graph, goals).items:
            if item.course_id is not None:
                assert item.course_id in catalog, f"{item.course_id} is not in the catalog"
                bound += 1
            for alternative in item.alternatives:
                assert alternative in catalog
    assert bound > 0, "the test is vacuous unless something was actually bound"


def test_free_only_yields_zero_paid_resources(graph) -> None:
    catalog = catalog_index()
    prefs = Preferences(cost_pref="free")
    rng = random.Random(SEED + 3)
    seen = 0

    for goals in random_goals(graph, 20, rng):
        plan = plan_for_goals(graph, goals, prefs=prefs)
        for item in plan.items:
            for resource_id in filter(None, [item.course_id, *item.alternatives]):
                assert catalog[resource_id].cost == "free"
                seen += 1
    assert seen >= 50, "not enough scored results to make the assertion meaningful"


def test_low_bandwidth_excludes_video(graph) -> None:
    catalog = catalog_index()
    plan = plan_for_goals(graph, ["web.fullstack_engineer"], prefs=Preferences(low_bandwidth=True))
    for item in plan.items:
        if item.course_id:
            assert catalog[item.course_id].format != "video"


def test_prior_knowledge_shortens_the_path(graph) -> None:
    """A web developer's ML path is shorter than a beginner's -- the shared core."""
    beginner = plan_for_goals(graph, ["ml.engineer"])

    experienced = MasteryTable()
    for skill_id in graph.required_for(["web.fullstack_engineer"]):
        experienced.set(skill_id, 1.0, "diagnostic")
    warm = plan_for_goals(graph, ["ml.engineer"], mastery=experienced)

    assert len(warm.items) < len(beginner.items)
    assert warm.finish_week < beginner.finish_week


# --------------------------------------------------------------------------- #
# Degenerate cases
# --------------------------------------------------------------------------- #
def test_fully_mastered_goal_returns_an_empty_path_cleanly(graph) -> None:
    mastered = MasteryTable()
    for skill_id in graph.required_for(["ml.engineer"]):
        mastered.set(skill_id, 1.0, "milestone")

    plan = plan_for_goals(graph, ["ml.engineer"], mastery=mastered)
    assert plan.items == []
    assert plan.finish_week == 0
    assert plan.total_hours == 0.0
    assert plan.weeks() == []


def test_unknown_goal_id_returns_an_empty_plan_not_an_exception(graph) -> None:
    plan = plan_for_goals(graph, ["not.a.real.node"])
    assert plan.items == []
    assert plan.goal_ids == []


def test_goal_that_is_its_own_root_still_plans(graph) -> None:
    plan = plan_for_goals(graph, ["web.html"])
    assert [i.skill_id for i in plan.items if i.kind == "resource"] == ["web.html"]


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_every_item_carries_a_complete_provenance_record(graph) -> None:
    plan = plan_for_goals(graph, ["ml.engineer"])
    for item in plan.items:
        if item.kind != "resource":
            continue
        provenance = item.provenance
        assert set(provenance) >= {"skill", "why_needed", "your_level", "why_this_resource", "placement"}
        assert provenance["placement"]["week"] == item.week_number
        assert provenance["why_this_resource"]["resource_id"] == item.course_id
        assert item.rationale_text, "a reason always exists, model or not"


def test_provenance_path_to_goal_is_a_real_chain(graph) -> None:
    plan = plan_for_goals(graph, ["ml.engineer"])
    for item in plan.items:
        chain = item.provenance.get("why_needed", {}).get("path_to_goal", [])
        previous = item.skill_id
        for step in chain:
            assert step in graph.children.get(previous, ()), (
                f"{step} is not a dependent of {previous}"
            )
            previous = step


def test_milestones_are_inserted_at_track_boundaries(graph) -> None:
    plan = plan_for_goals(graph, ["ml.engineer"])
    milestones = [i for i in plan.items if i.kind == "milestone"]
    tracks = {i.provenance["track"] for i in plan.items if i.kind == "resource"}

    assert milestones, "a multi-track path needs checkpoints"
    assert len(milestones) == len(tracks)
    assert all(m.provenance["milestone"]["questions"] == 3 for m in milestones)


# --------------------------------------------------------------------------- #
# What the explanation is allowed to say
# --------------------------------------------------------------------------- #
def test_the_reason_names_skills_rather_than_reciting_ids() -> None:
    """A learner was told a step "leads into astronomy.basic_geometry".

    That is a database key. The chain of ids stays in the provenance, because
    the trace panel is meant to be machine-exact, but the sentence a person
    reads is in words.
    """
    from app.core.explain import render_template

    provenance = {
        "skill": "astronomy.unit_conversions",
        "skill_name": "Unit Conversions",
        "why_needed": {
            "goal": "Basic Astronomy Telescopes",
            "path_to_goal": ["astronomy.basic_geometry", "astronomy.basic_physics"],
            "path_to_goal_names": ["Basic Geometry", "Basic Physics"],
            "is_goal": False,
        },
        "your_level": {"score": 0.15, "source": "diagnostic"},
        "why_this_resource": {
            "title": "List of conversion factors", "reasons": ["free to access"],
            "beat_alternatives": 2,
        },
        "placement": {"week": 1, "hours": 1.0, "unlock_count": 8},
    }
    sentence = render_template(provenance)
    assert "Basic Geometry then Basic Physics" in sentence
    assert "astronomy." not in sentence


def test_a_plan_built_before_names_existed_still_explains_itself() -> None:
    """Stored provenance is old data; it must degrade, not crash."""
    from app.core.explain import render_template

    provenance = {
        "skill": "prog.python_basics",
        "skill_name": "Python Basics",
        "why_needed": {"goal": "ML Engineer", "path_to_goal": ["ml.numpy"], "is_goal": False},
        "your_level": {"score": 0.4, "source": "self"},
        "why_this_resource": {"title": "A course", "reasons": [], "beat_alternatives": 0},
        "placement": {"week": 2, "hours": 3.0, "unlock_count": 2},
    }
    assert "ml.numpy" in render_template(provenance)
