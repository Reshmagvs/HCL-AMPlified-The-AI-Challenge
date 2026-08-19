"""Phase 1 acceptance: the graph is a valid DAG and ordering never violates it.

The last test here is the flagship property. Everything the planner does rests
on the claim that a returned ordering can actually be followed, so it is checked
by construction over 100 randomly chosen goal sets rather than on one example.
"""

from __future__ import annotations

import json
import random

import pytest

from app.core.skill_graph import (
    DanglingPrerequisiteError,
    DuplicateNodeError,
    GraphCycleError,
    build_graph,
)

# A hand-built 8-node fixture with a known ancestor closure.
#
#   a ──> c ──> e ──> g
#   b ──> c        ┌──┘
#   b ──> d ──> f ─┘        h stands alone
FIXTURE = [
    {"id": "a", "name": "A", "track": "t", "prerequisites": [], "difficulty": 1, "est_hours": 1},
    {"id": "b", "name": "B", "track": "t", "prerequisites": [], "difficulty": 1, "est_hours": 1},
    {"id": "c", "name": "C", "track": "t", "prerequisites": ["a", "b"], "difficulty": 2, "est_hours": 1},
    {"id": "d", "name": "D", "track": "t", "prerequisites": ["b"], "difficulty": 2, "est_hours": 1},
    {"id": "e", "name": "E", "track": "t", "prerequisites": ["c"], "difficulty": 3, "est_hours": 1},
    {"id": "f", "name": "F", "track": "t", "prerequisites": ["d"], "difficulty": 3, "est_hours": 1},
    {"id": "g", "name": "G", "track": "t", "prerequisites": ["e", "f"], "difficulty": 4, "est_hours": 1},
    {"id": "h", "name": "H", "track": "t", "prerequisites": [], "difficulty": 1, "est_hours": 1},
]


@pytest.fixture(scope="module")
def fixture_graph():
    return build_graph(FIXTURE)


# --------------------------------------------------------------------------- #
# Fixture behaviour
# --------------------------------------------------------------------------- #
def test_ancestors_closure_matches_expected_set(fixture_graph) -> None:
    assert fixture_graph.ancestors_closure(["g"]) == {"a", "b", "c", "d", "e", "f"}
    assert fixture_graph.ancestors_closure(["e"]) == {"a", "b", "c"}
    assert fixture_graph.ancestors_closure(["h"]) == set()
    assert fixture_graph.required_for(["e"]) == {"a", "b", "c", "e"}


def test_downstream_unlock_count(fixture_graph) -> None:
    assert fixture_graph.downstream_unlock_count("b") == 5  # c, d, e, f, g
    assert fixture_graph.downstream_unlock_count("g") == 0
    assert fixture_graph.downstream_unlock_count("h") == 0


def test_depth(fixture_graph) -> None:
    assert fixture_graph.depth("a") == 0
    assert fixture_graph.depth("c") == 1
    assert fixture_graph.depth("g") == 3


def test_topological_sort_is_deterministic(fixture_graph) -> None:
    first = fixture_graph.topological_sort()
    for _ in range(10):
        assert fixture_graph.topological_sort() == first


# --------------------------------------------------------------------------- #
# Integrity errors are loud and specific
# --------------------------------------------------------------------------- #
def test_duplicate_id_is_rejected() -> None:
    with pytest.raises(DuplicateNodeError, match="a"):
        build_graph([*FIXTURE, dict(FIXTURE[0])])


def test_dangling_prerequisite_is_rejected() -> None:
    broken = [*FIXTURE, {"id": "z", "name": "Z", "track": "t",
                         "prerequisites": ["nope"], "difficulty": 1, "est_hours": 1}]
    with pytest.raises(DanglingPrerequisiteError, match="nope"):
        build_graph(broken)


def test_cycle_is_rejected() -> None:
    cyclic = [
        {"id": "x", "name": "X", "track": "t", "prerequisites": ["y"], "difficulty": 1, "est_hours": 1},
        {"id": "y", "name": "Y", "track": "t", "prerequisites": ["x"], "difficulty": 1, "est_hours": 1},
    ]
    with pytest.raises(GraphCycleError):
        build_graph(cyclic)


def test_self_prerequisite_is_rejected() -> None:
    with pytest.raises(GraphCycleError, match="itself"):
        build_graph([{"id": "s", "name": "S", "track": "t",
                      "prerequisites": ["s"], "difficulty": 1, "est_hours": 1}])


# --------------------------------------------------------------------------- #
# The real graph
# --------------------------------------------------------------------------- #
def test_real_graph_loads_and_is_substantial(graph) -> None:
    assert len(graph) >= 120, "the graph needs ~120 nodes to be worth sequencing"
    assert {"machine-learning", "web-development", "data-analytics",
            "cybersecurity", "cloud-devops"} <= set(graph.tracks)


def test_real_graph_has_no_duplicate_ids() -> None:
    from app.config import get_settings

    raw = json.loads((get_settings().data_dir / "skills.json").read_text(encoding="utf-8"))
    ids = [entry["id"] for entry in raw]
    assert len(ids) == len(set(ids))


def test_every_prerequisite_resolves(graph) -> None:
    for node in graph.nodes.values():
        for prereq in node.prerequisites:
            assert prereq in graph, f"{node.id} -> missing {prereq}"


def test_every_track_has_real_depth(graph) -> None:
    """A flat graph makes the product pointless; each track needs 4+ levels."""
    for track in graph.tracks:
        depths = [graph.depth(n.id) for n in graph.by_track(track)]
        assert max(depths) >= 4, f"track {track} is only {max(depths)} levels deep"


def test_shared_foundations_are_reached_from_multiple_tracks(graph) -> None:
    """The shared core is what makes a web developer's ML path shorter."""
    shared = ["prog.python_basics", "prog.git", "math.linear_algebra",
              "math.statistics", "cs.data_structures", "web.http"]
    for skill_id in shared:
        assert skill_id in graph, f"missing shared foundation {skill_id}"
        tracks = {graph.require(d).track for d in graph.descendants(skill_id)}
        assert len(tracks) >= 2, f"{skill_id} only feeds {tracks}"


def test_zero_prerequisite_violations_across_100_random_orderings(graph) -> None:
    """The flagship property, checked over 100 randomly chosen goal sets."""
    rng = random.Random(20260819)
    all_ids = sorted(graph.nodes)
    violations: list[str] = []

    for _ in range(100):
        goals = rng.sample(all_ids, rng.randint(1, 3))
        subset = graph.required_for(goals)
        order = graph.topological_sort(subset)

        assert set(order) == subset
        assert len(order) == len(set(order))

        position = {skill: i for i, skill in enumerate(order)}
        for skill in order:
            for prereq in graph.require(skill).prerequisites:
                if prereq in position and position[prereq] > position[skill]:
                    violations.append(f"{prereq} after {skill}")

    assert violations == [], f"{len(violations)} prerequisite violations: {violations[:5]}"
