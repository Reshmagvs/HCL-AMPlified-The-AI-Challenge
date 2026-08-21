"""Deterministic extraction: the floor the intake conversation stands on.

These cases came out of driving the real interface. Two of them are regressions
for defects a learner would have hit on their very first message: a subject
typed on its own yielded no goal at all, and two ordinary ways of stating a
weekly budget ("4 hours weekly", "6 hrs/wk") were not recognised.

The extractor is deliberately conservative, so half of what is asserted here is
what it must *not* invent.
"""

from __future__ import annotations

import pytest

from app.core.text_profile import extract_profile, next_question


# --------------------------------------------------------------------------- #
# A subject named on its own is still a goal
# --------------------------------------------------------------------------- #
BARE_SUBJECTS = [
    ("organic chemistry for my class 12 board exam, 6 hours a week, free only",
     "organic chemistry for my class 12 board exam"),
    ("quantum computing", "quantum computing"),
    ("medieval european history. about 5 hours a week", "medieval european history"),
]


@pytest.mark.parametrize("message,expected", BARE_SUBJECTS)
def test_a_subject_typed_on_its_own_is_taken_as_the_goal(message: str, expected: str) -> None:
    """The regression this fallback exists for.

    Without it the learner named their subject and was immediately asked what
    they wanted to study.
    """
    assert extract_profile(message).get("goal_text") == expected


def test_the_established_goal_survives_a_later_message() -> None:
    """A follow-up about constraints must not be mistaken for a new subject."""
    first = extract_profile("organic chemistry, 6 hours a week")
    second = extract_profile("I prefer video, and free material only", first)
    assert second["goal_text"] == "organic chemistry"


CONSTRAINT_ONLY = [
    "6 hours a week, free only",
    "hello there",
    "hi",
    "thanks, that sounds good",
    "I already know Python and git",
    "beginner",
    "I'm on limited data so text is better",
]


@pytest.mark.parametrize("message", CONSTRAINT_ONLY)
def test_a_message_that_states_only_constraints_yields_no_goal(message: str) -> None:
    """Inventing a goal out of "6 hours a week" -- or out of "hello there" -- is
    worse than having none."""
    assert extract_profile(message).get("goal_text") is None


def test_explicit_goal_phrasing_still_wins() -> None:
    """The fallback is a fallback; it must not displace the phrasing rule."""
    profile = extract_profile("I'm a second year CS student. I want to become an ML engineer.")
    assert profile["goal_text"] == "an ML engineer"


# --------------------------------------------------------------------------- #
# Weekly budgets, however they are phrased
# --------------------------------------------------------------------------- #
HOURS = [
    ("6 hours a week", 6.0),
    ("about 8 hrs per week", 8.0),
    ("I can do 10 hours each week", 10.0),
    ("4 hours weekly", 4.0),
    ("6 hrs/wk", 6.0),
    ("12 h weekly", 12.0),
]


@pytest.mark.parametrize("message,expected", HOURS)
def test_weekly_hours_are_read_from_ordinary_phrasings(message: str, expected: float) -> None:
    assert extract_profile(message).get("hours_per_week") == expected


NOT_HOURS = [
    "I study in the evenings",
    "it took me 200 hours to learn python",  # a claim about the past, not a budget
    "0 hours a week",                        # not a usable budget
]


@pytest.mark.parametrize("message", NOT_HOURS)
def test_hours_are_not_guessed(message: str) -> None:
    assert extract_profile(message).get("hours_per_week") is None


# --------------------------------------------------------------------------- #
# The question that follows
# --------------------------------------------------------------------------- #
def test_the_next_question_never_asks_what_was_just_answered() -> None:
    profile = extract_profile("organic chemistry for my class 12 board exam, 6 hours a week")
    question = next_question(profile)
    assert "what would you like to be able to do" not in question.lower()
    assert "hours a week" not in question.lower()
