"""Phase 5 acceptance: the mastery update rules, tested without a database."""

from __future__ import annotations

import pytest

from app.core.mastery import (
    MASTERY_THRESHOLD,
    SELF_REPORT_CAP,
    MasteryTable,
    apply_answer,
    confidence,
    grade_answer,
)


def test_unmeasured_is_zero() -> None:
    table = MasteryTable()
    assert table.score("anything") == 0.0
    assert table.get("anything").source == "unmeasured"
    assert not table.is_mastered("anything")


def test_self_report_is_capped_at_exactly_the_cap() -> None:
    table = MasteryTable()
    table.set("prog.git", 1.0, "self")
    assert table.score("prog.git") == pytest.approx(SELF_REPORT_CAP)
    assert table.score("prog.git") < MASTERY_THRESHOLD


def test_a_weaker_source_never_overwrites_a_stronger_one() -> None:
    table = MasteryTable()
    table.set("prog.git", 1.0, "diagnostic")
    assert table.set("prog.git", 0.4, "self") is False
    assert table.score("prog.git") == 1.0

    table.set("prog.git", 0.2, "milestone")
    assert table.set("prog.git", 1.0, "diagnostic") is False
    assert table.score("prog.git") == 0.2


def test_force_lets_adaptation_correct_a_measurement() -> None:
    table = MasteryTable()
    table.set("prog.git", 1.0, "milestone")
    assert table.set("prog.git", 0.25, "milestone", force=True) is True
    assert table.score("prog.git") == 0.25


def test_grading_distinguishes_a_wrong_answer_from_an_abstention() -> None:
    correct = grade_answer(correct=True, dont_know=False)
    wrong = grade_answer(correct=False, dont_know=False)
    unknown = grade_answer(correct=False, dont_know=True)

    assert correct == 1.0
    assert unknown < wrong < MASTERY_THRESHOLD


def test_a_passed_question_reaches_one(graph) -> None:
    table = MasteryTable()
    apply_answer(table, graph, skill_id="ml.gradient_descent", correct=True,
                 dont_know=False, question_id=1)
    assert table.score("ml.gradient_descent") == 1.0
    assert table.is_mastered("ml.gradient_descent")


def test_correct_answers_nudge_prerequisites_but_never_set_them(graph) -> None:
    """Indirect evidence must not on its own remove a skill from the path."""
    table = MasteryTable()
    apply_answer(table, graph, skill_id="ml.backpropagation", correct=True,
                 dont_know=False, question_id=7)

    for prereq in graph.require("ml.backpropagation").prerequisites:
        score = table.score(prereq)
        assert 0.0 < score < MASTERY_THRESHOLD, f"{prereq} nudged to {score}"


def test_a_nudge_decays_with_distance(graph) -> None:
    table = MasteryTable()
    apply_answer(table, graph, skill_id="ml.backpropagation", correct=True,
                 dont_know=False, question_id=7)

    one_hop = table.score("ml.neural_nets")
    two_hop = table.score("ml.gradient_descent")
    assert one_hop > two_hop > 0.0


def test_a_nudge_never_overwrites_a_milestone(graph) -> None:
    table = MasteryTable()
    table.set("math.derivatives", 0.1, "milestone")
    apply_answer(table, graph, skill_id="ml.backpropagation", correct=True,
                 dont_know=False, question_id=7)
    assert table.score("math.derivatives") == 0.1


def test_a_wrong_answer_nudges_nothing(graph) -> None:
    table = MasteryTable()
    apply_answer(table, graph, skill_id="ml.backpropagation", correct=False,
                 dont_know=False, question_id=7)
    assert table.score("ml.neural_nets") == 0.0


def test_gap_is_everything_below_threshold() -> None:
    table = MasteryTable()
    table.set("a", 0.9, "diagnostic")
    table.set("b", 0.69, "diagnostic")
    assert table.gap({"a", "b", "c"}) == {"b", "c"}


def test_confidence_rises_as_skills_are_measured(graph) -> None:
    gap = set(graph.required_for(["ml.engineer"]))
    table = MasteryTable()
    start = confidence(table, gap, graph)

    for index, skill_id in enumerate(sorted(gap)[:20]):
        apply_answer(table, graph, skill_id=skill_id, correct=True,
                     dont_know=False, question_id=index)
    assert confidence(table, gap, graph) > start


def test_confidence_of_an_empty_gap_is_one(graph) -> None:
    """So the diagnostic terminates rather than looping on nothing to ask about."""
    assert confidence(MasteryTable(), set(), graph) == 1.0
