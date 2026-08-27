"""Subjects must not leak into each other.

Every test here is a regression for one reported failure: a learner asked for
business studies, was given a curriculum of statistics, SQL and machine
learning, and was then placement-tested on Python. Three separate defects
combined to produce it, and each is pinned down below.
"""

from __future__ import annotations

import pytest

from app.core.expansion import ProposedSkill, _material_word, _search_query
from app.core.skill_graph import load_graph
from app.core.text_profile import missing_field, next_question
from app.resolution import resolve_goal


# --------------------------------------------------------------------------- #
# 1. Resolution must not cross a domain boundary
# --------------------------------------------------------------------------- #
FOREIGN = [
    "medieval european history",
    "learn to play the piano",
    "photosynthesis and cell biology",
]


@pytest.mark.parametrize("goal", FOREIGN)
def test_a_subject_the_curriculum_does_not_teach_resolves_to_nothing(goal: str, graph) -> None:
    """The defect this exists to prevent, in its purest form.

    Nearest-neighbour over a closed set always returns *something*, so a goal
    with no home in the curriculum was handed the closest thing available --
    and a plan for the wrong subject is worse than no plan, because the learner
    cannot tell it is wrong until they have followed it.
    """
    chosen, _candidates, _degraded = resolve_goal(goal)
    assert chosen == [], f"{goal!r} should need building, not the nearest curated node"


COVERED = [
    "become a machine learning engineer",
    "I want to build websites end to end",
    "learn cybersecurity",
]


@pytest.mark.parametrize("goal", COVERED)
def test_goals_the_curriculum_does_teach_still_resolve(goal: str, graph) -> None:
    """The over-correction, which is just as bad.

    A first version refused anything that was not *confidently* covered, which
    rejected goals the curriculum genuinely teaches whenever the two coverage
    signals merely disagreed. Refusal needs the stronger verdict: both signals
    rejecting it.
    """
    chosen, _candidates, _degraded = resolve_goal(goal)
    assert chosen, f"{goal!r} is taught here and must resolve"


# --------------------------------------------------------------------------- #
# 2. Material is searched for the way the subject is actually learned
# --------------------------------------------------------------------------- #
def test_a_studied_subject_is_not_sent_looking_for_tutorials() -> None:
    """"Tutorial" means software walkthrough on the open web."""
    assert "tutorial" not in _material_word({"technical": False})
    assert _material_word({"technical": True}) == "tutorial"


def test_a_quantitative_subject_asks_for_practice() -> None:
    assert "practice" in _material_word({"quantitative": True})
    assert "practice" in _material_word({"practical": True})


def test_the_search_query_reflects_the_subject_kind() -> None:
    skill = ProposedSkill(name="Interpret a Balance Sheet", keywords=["balance sheet", "assets"])
    studied = _search_query(skill, "Business Studies", {"technical": False})
    coded = _search_query(skill, "Software Engineering", {"technical": True})
    assert studied.endswith("explained introduction")
    assert coded.endswith("tutorial")


# --------------------------------------------------------------------------- #
# 3. A skill carries the subject it belongs to
# --------------------------------------------------------------------------- #
def test_a_curated_node_reports_a_subject(graph) -> None:
    """Question generation names it, so it can never be empty."""
    node = next(iter(load_graph().nodes.values()))
    assert node.subject


# --------------------------------------------------------------------------- #
# 4. The assistant does not repeat itself
# --------------------------------------------------------------------------- #
def test_asking_twice_does_not_ask_the_same_way_twice() -> None:
    """The reported bug: "the messages keep repeating".

    The rules had no memory of having already asked, so a learner whose answer
    could not be parsed saw the identical sentence every turn.
    """
    profile = {"goal_text": "business studies"}
    asked = [next_question(profile, attempt) for attempt in range(3)]
    assert len(set(asked)) == 3, asked


def test_a_later_attempt_offers_a_concrete_example() -> None:
    """Escalation has to add something, not merely reword."""
    assert "for example" in next_question({"goal_text": "x"}, 2).lower()


def test_a_complete_profile_does_not_repeat_one_sentence() -> None:
    complete = {
        "goal_text": "business studies",
        "hours_per_week": 5.0,
        "experience_level": "beginner",
        "cost_pref": "free",
    }
    assert missing_field(complete) is None
    said = [next_question(complete, attempt) for attempt in range(3)]
    assert len(set(said)) == 3, said


def test_the_field_being_chased_is_the_first_one_missing() -> None:
    assert missing_field({}) == "goal_text"
    assert missing_field({"goal_text": "x"}) == "hours_per_week"
    assert missing_field({"goal_text": "x", "hours_per_week": 5}) == "experience_level"
    # "any" is the absence of a preference, not a preference.
    assert missing_field(
        {"goal_text": "x", "hours_per_week": 5, "experience_level": "beginner", "cost_pref": "any"}
    ) == "cost_pref"
