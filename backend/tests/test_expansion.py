"""Open-world expansion: coverage, syllabus sanitising, and the overlay.

Nothing here touches the network or a language model. The parts that do are
isolated behind ``design_syllabus`` and ``websearch``, and both are substituted.
What is tested is the logic that decides *whether* to build, what shape the
result is allowed to take, and whether a bad generation can damage anything.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core import expansion, retrieval, store
from app.core.expansion import ProposedSkill, ProposedSyllabus
from app.core.skill_graph import load_graph


@pytest.fixture(autouse=True)
def clean_overlay():
    """Every test starts and ends with no discovered content."""
    store.clear()
    expansion.invalidate()
    yield
    store.clear()
    expansion.invalidate()


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #
COVERED = [
    "become a machine learning engineer",
    "learn python programming",
    "I want to build websites",
    "cybersecurity penetration testing",
    "data analysis with SQL",
    "deploy apps with docker and kubernetes",
]
NOT_COVERED = [
    "I want to understand quantum computing",
    "organic chemistry for my exams",
    "medieval european history",
    "learn to play the piano",
    "photosynthesis and cell biology",
    "learn spanish grammar",
]


@pytest.mark.parametrize("goal", COVERED)
def test_goals_the_curriculum_teaches_are_recognised(goal: str) -> None:
    assert expansion.assess_coverage(goal).covered, goal


@pytest.mark.parametrize("goal", NOT_COVERED)
def test_goals_outside_the_curriculum_are_not_forced_onto_a_near_neighbour(goal: str) -> None:
    """The failure this whole module exists to prevent.

    Before it, "quantum computing" resolved to whatever node happened to be
    nearest and the learner was handed a confident plan for the wrong subject.
    """
    assert not expansion.assess_coverage(goal).covered, goal


def test_coverage_explains_itself_in_words() -> None:
    verdict = expansion.assess_coverage("learn to play the piano")
    assert verdict.reason
    assert not verdict.covered


def test_unknown_words_dominate_generic_ones() -> None:
    """Familiarity is weighted, or "structural analysis" reads as covered."""
    assert expansion._lexical_familiarity("python programming") > 0.8
    assert expansion._lexical_familiarity("photosynthesis chloroplast") < 0.2


# --------------------------------------------------------------------------- #
# Syllabus sanitising
# --------------------------------------------------------------------------- #
def _syllabus(skills: list[ProposedSkill]) -> ProposedSyllabus:
    return ProposedSyllabus(topic="Test Topic", track="test", skills=skills)


def test_a_cycle_cannot_be_expressed() -> None:
    """Prerequisites may only point backwards, so the DAG cannot be broken."""
    proposed = _syllabus([
        ProposedSkill(name="Aa", requires=[1, 2]),
        ProposedSkill(name="Bb", requires=[2]),
        ProposedSkill(name="Cc", requires=[0]),
    ])
    cleaned = expansion._sanitise(proposed)
    for index, skill in enumerate(cleaned):
        assert all(r < index for r in skill.requires)


def test_duplicate_names_are_dropped_and_references_follow() -> None:
    proposed = _syllabus([
        ProposedSkill(name="Alpha"),
        ProposedSkill(name="alpha  "),          # the same skill, differently typed
        ProposedSkill(name="Beta", requires=[0]),
    ])
    cleaned = expansion._sanitise(proposed)
    assert [s.name for s in cleaned] == ["Alpha", "Beta"]
    assert cleaned[1].requires == [0]


def test_out_of_range_values_are_clamped() -> None:
    cleaned = expansion._sanitise(_syllabus([
        ProposedSkill(name="Aa", difficulty=99, hours=10_000),
        ProposedSkill(name="Bb", difficulty=-4, hours=0.0001, requires=[0]),
    ]))
    assert all(1 <= s.difficulty <= expansion.MAX_DIFFICULTY for s in cleaned)
    assert all(expansion.MIN_HOURS <= s.hours <= expansion.MAX_HOURS for s in cleaned)


def test_a_syllabus_with_no_dependencies_becomes_a_chain() -> None:
    """A reading list is not a path; the fallback ordering is still an ordering."""
    cleaned = expansion._sanitise(_syllabus([ProposedSkill(name=n) for n in ("Aa", "Bb", "Cc", "Dd")]))
    assert [s.requires for s in cleaned] == [[], [0], [1], [2]]


def test_machine_shaped_names_are_made_readable() -> None:
    """The model sometimes answers in the shape of the ids around it."""
    assert expansion.readable("quantum-gates") == "Quantum Gates"
    assert expansion.readable("binary_numbers") == "Binary Numbers"
    assert expansion.readable("Qubits and Superposition") == "Qubits and Superposition"
    assert expansion.readable("IUPAC nomenclature") == "IUPAC nomenclature"


def test_sanitising_produces_readable_names() -> None:
    cleaned = expansion._sanitise(_syllabus([ProposedSkill(name="quantum-error-correction")]))
    assert cleaned[0].name == "Quantum Error Correction"


def test_terminal_skills_become_the_goal() -> None:
    skills = [
        ProposedSkill(name="Aa"),
        ProposedSkill(name="Bb", requires=[0]),
        ProposedSkill(name="Cc", requires=[0]),
    ]
    ids = ["t.a", "t.b", "t.c"]
    assert expansion._terminal_ids(ids, skills) == ["t.b", "t.c"]


# --------------------------------------------------------------------------- #
# The overlay
# --------------------------------------------------------------------------- #
def test_the_suite_cannot_clear_a_real_installation() -> None:
    """A regression test for damage this suite actually did.

    ``clean_overlay`` calls ``store.clear()`` around every test in this file.
    While the overlay location was fixed at ``data/generated`` that deleted every
    subject the developer had built, silently, on any test run.
    """
    from app.config import get_settings

    assert get_settings().generated_dir, "tests must not write to the default overlay"
    assert store.generated_dir() != get_settings().data_dir / "generated"



def _store_topic(skills: list[dict], courses: list[dict], goals: list[str]) -> None:
    dim = retrieval.load_matrices()["dim"]
    store.append_topic(
        goal_text="a test goal",
        topic_name="Test Topic",
        track="test",
        goal_skill_ids=goals,
        skills=skills,
        courses=courses,
        skill_vectors={s["id"]: np.zeros(dim, dtype=np.float32) for s in skills},
        course_vectors={c["id"]: np.zeros(dim, dtype=np.float32) for c in courses},
        embedder=retrieval.load_matrices()["embedder"],
        stats={},
    )
    expansion.invalidate()


def test_a_stored_topic_joins_the_graph_and_catalogue() -> None:
    before_skills, before_catalog = len(load_graph()), len(retrieval.load_catalog())
    _store_topic(
        skills=[{
            "id": "test.alpha", "name": "Alpha", "track": "test", "description": "d",
            "prerequisites": [], "difficulty": 1, "est_hours": 2.0,
            "assessable": True, "keywords": ["alpha"], "discovered": True,
        }],
        courses=[{
            "id": "test_res_1", "title": "Alpha Guide", "provider": "example.org",
            "url": "https://example.org/alpha", "format": "text", "cost": "free",
            "duration_hours": 1.0, "level": "beginner", "skills_covered": ["test.alpha"],
            "rating": None, "language": "en", "description": "d", "discovered": True,
        }],
        goals=["test.alpha"],
    )
    assert len(load_graph()) == before_skills + 1
    assert len(retrieval.load_catalog()) == before_catalog + 1
    assert retrieval.catalog_index()["test_res_1"].discovered is True


def test_the_seed_is_never_overwritten_by_the_overlay() -> None:
    """A generated node may not redefine a curated one, however it is named."""
    curated = next(iter(load_graph().nodes.values()))
    _store_topic(
        skills=[{
            "id": curated.id, "name": "Hijacked", "track": "test", "description": "x",
            "prerequisites": [], "difficulty": 5, "est_hours": 99.0,
            "assessable": True, "keywords": [], "discovered": True,
        }],
        courses=[],
        goals=[curated.id],
    )
    assert load_graph().require(curated.id).name == curated.name


def test_a_generated_node_with_a_dangling_prerequisite_is_dropped_not_fatal() -> None:
    """One bad topic must not take down the graph for everybody else."""
    _store_topic(
        skills=[{
            "id": "test.orphan", "name": "Orphan", "track": "test", "description": "d",
            "prerequisites": ["test.does_not_exist"], "difficulty": 1, "est_hours": 1.0,
            "assessable": True, "keywords": [], "discovered": True,
        }],
        courses=[],
        goals=["test.orphan"],
    )
    graph = load_graph()
    assert "test.orphan" not in graph
    assert len(graph) >= 152  # the seed is intact


def test_matrices_stay_aligned_after_the_graph_grows() -> None:
    """The row-per-id contract is what stops retrieval silently mismatching."""
    _store_topic(
        skills=[{
            "id": "test.beta", "name": "Beta", "track": "test", "description": "d",
            "prerequisites": [], "difficulty": 1, "est_hours": 1.0,
            "assessable": True, "keywords": ["beta"], "discovered": True,
        }],
        courses=[],
        goals=["test.beta"],
    )
    matrices = retrieval.load_matrices()
    assert matrices["skills"].shape[0] == len(matrices["skill_ids"])
    assert matrices["skill_ids"][matrices["skill_row"]["test.beta"]] == "test.beta"


def test_a_built_topic_is_cached_by_its_goal_text() -> None:
    _store_topic(
        skills=[{
            "id": "test.gamma", "name": "Gamma", "track": "test", "description": "d",
            "prerequisites": [], "difficulty": 1, "est_hours": 1.0,
            "assessable": True, "keywords": [], "discovered": True,
        }],
        courses=[],
        goals=["test.gamma"],
    )
    assert store.find_topic("A Test Goal") is not None      # case-insensitive
    assert store.find_topic("  a   test goal ") is not None  # whitespace-insensitive
    assert store.find_topic("something else") is None
