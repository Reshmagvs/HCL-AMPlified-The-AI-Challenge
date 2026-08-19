"""The skill dependency graph -- the intellectual core of the product.

The graph is a DAG whose edges mean *pedagogical necessity*: an edge
``a -> b`` asserts that ``b`` cannot be understood before ``a``. Topical
similarity is explicitly not an edge; "both are machine learning" is a track
label, not a dependency.

Three operations are built on it, and everything downstream is a composition of
them:

``ancestors_closure(goals)``
    Reverse reachability. A breadth-first walk up the prerequisite edges giving
    the complete set of skills a goal transitively requires. This is the whole
    of "what do I need to know", computed in O(V+E) with no model involved.

``topological_sort(subset)``
    Kahn's algorithm restricted to a subset, with a deterministic tie-break.
    Whenever several nodes are simultaneously unblocked, the ordering prefers:
    (1) fewer prerequisites inside the subset, (2) lower difficulty, (3) more
    downstream unlocks, (4) lexicographic id. The final key guarantees a total
    order, so identical input yields a byte-identical path -- a property the
    planner's determinism test depends on.

``downstream_unlock_count(node)``
    Forward reachability size. How many other skills this one gates. It drives
    both the ordering tie-break (teach the bottleneck early) and diagnostic item
    selection (measure the node that resolves the most uncertainty).

Validation runs once at load. Cycles, dangling prerequisites and duplicate ids
raise loud, specific exceptions naming the offending nodes, because a silent
graph defect surfaces later as a wrong learning path with no obvious cause.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


class GraphIntegrityError(ValueError):
    """Base class for every structural defect in skills.json."""


class DuplicateNodeError(GraphIntegrityError):
    """Two nodes share an id."""


class DanglingPrerequisiteError(GraphIntegrityError):
    """A prerequisite id does not resolve to a node."""


class GraphCycleError(GraphIntegrityError):
    """The prerequisite relation contains a cycle."""


@dataclass(frozen=True)
class SkillNode:
    """One learnable skill."""

    id: str
    name: str
    track: str
    description: str
    prerequisites: tuple[str, ...]
    difficulty: int
    est_hours: float
    assessable: bool
    keywords: tuple[str, ...]

    @property
    def embed_text(self) -> str:
        """The text used to embed this node for goal resolution and binding."""
        return f"{self.name}. {self.description} Topics: {', '.join(self.keywords)}."


@dataclass
class SkillGraph:
    """A validated DAG of skills with precomputed derived data."""

    nodes: dict[str, SkillNode]
    children: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unlocks: dict[str, int] = field(default_factory=dict, repr=False)

    # -- basics -------------------------------------------------------------
    def __contains__(self, node_id: object) -> bool:
        return node_id in self.nodes

    def __len__(self) -> int:
        return len(self.nodes)

    def get(self, node_id: str) -> SkillNode | None:
        return self.nodes.get(node_id)

    def require(self, node_id: str) -> SkillNode:
        """Fetch a node or fail with a message naming the missing id."""
        node = self.nodes.get(node_id)
        if node is None:
            raise KeyError(f"unknown skill id: {node_id!r}")
        return node

    @property
    def tracks(self) -> list[str]:
        return sorted({n.track for n in self.nodes.values()})

    def by_track(self, track: str) -> list[SkillNode]:
        return sorted((n for n in self.nodes.values() if n.track == track), key=lambda n: n.id)

    # -- traversal ----------------------------------------------------------
    def ancestors_closure(self, goal_ids: Iterable[str]) -> set[str]:
        """Every skill transitively required by goal_ids (the goals themselves excluded)."""
        seen: set[str] = set()
        queue: deque[str] = deque()
        for goal in goal_ids:
            queue.extend(self.require(goal).prerequisites)
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(self.require(current).prerequisites)
        return seen

    def required_for(self, goal_ids: Iterable[str]) -> set[str]:
        """ancestors_closure(goals) | goals -- the full requirement set."""
        goals = list(goal_ids)
        return self.ancestors_closure(goals) | set(goals)

    def descendants(self, node_id: str) -> set[str]:
        """Every skill that transitively depends on node_id."""
        seen: set[str] = set()
        queue: deque[str] = deque(self.children.get(node_id, ()))
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(self.children.get(current, ()))
        return seen

    def downstream_unlock_count(self, node_id: str) -> int:
        """How many skills this one gates. Precomputed at load."""
        return self.unlocks.get(node_id, 0)

    def depth(self, node_id: str) -> int:
        """Longest prerequisite chain ending at this node (roots are depth 0)."""
        memo: dict[str, int] = {}

        def walk(current: str) -> int:
            if current in memo:
                return memo[current]
            prereqs = self.require(current).prerequisites
            memo[current] = 1 + max((walk(p) for p in prereqs), default=-1)
            return memo[current]

        return walk(node_id)

    # -- ordering -----------------------------------------------------------
    def topological_sort(self, subset: Iterable[str] | None = None) -> list[str]:
        """Kahn's algorithm over subset with the documented deterministic tie-break.

        Only edges *inside* the subset constrain the ordering. A prerequisite
        outside the subset is by construction already satisfied: the planner only
        ever passes a gap set, and anything excluded from it is already mastered.
        """
        members = set(self.nodes) if subset is None else {s for s in subset if s in self.nodes}
        indegree = {
            node: sum(1 for p in self.nodes[node].prerequisites if p in members) for node in members
        }
        rank = {
            node: (
                indegree[node],
                self.nodes[node].difficulty,
                -self.downstream_unlock_count(node),
                node,
            )
            for node in members
        }
        ready = sorted((n for n in members if indegree[n] == 0), key=rank.__getitem__)
        remaining = dict(indegree)
        order: list[str] = []

        while ready:
            current = ready.pop(0)
            order.append(current)
            newly: list[str] = []
            for child in self.children.get(current, ()):
                if child not in members:
                    continue
                remaining[child] -= 1
                if remaining[child] == 0:
                    newly.append(child)
            if newly:
                ready = sorted([*ready, *newly], key=rank.__getitem__)

        if len(order) != len(members):
            stuck = sorted(members - set(order))
            raise GraphCycleError(f"cycle detected among skills: {stuck[:12]}")
        return order


# --------------------------------------------------------------------------- #
# Loading and validation
# --------------------------------------------------------------------------- #
_REQUIRED_FIELDS = ("id", "name", "track", "prerequisites", "difficulty", "est_hours")


def _build_nodes(raw: list[dict[str, Any]]) -> dict[str, SkillNode]:
    """Turn the JSON list into nodes, failing loudly on any structural defect."""
    nodes: dict[str, SkillNode] = {}
    for index, entry in enumerate(raw):
        missing = [f for f in _REQUIRED_FIELDS if f not in entry]
        if missing:
            raise GraphIntegrityError(f"skills.json entry {index} is missing {missing}")
        node_id = entry["id"]
        if node_id in nodes:
            raise DuplicateNodeError(f"duplicate skill id {node_id!r} at entry {index}")
        nodes[node_id] = SkillNode(
            id=node_id,
            name=entry["name"],
            track=entry["track"],
            description=entry.get("description", ""),
            prerequisites=tuple(dict.fromkeys(entry["prerequisites"])),
            difficulty=int(entry["difficulty"]),
            est_hours=float(entry["est_hours"]),
            assessable=bool(entry.get("assessable", True)),
            keywords=tuple(entry.get("keywords", [])),
        )
    return nodes


def _check_edges(nodes: dict[str, SkillNode]) -> None:
    """Every prerequisite must resolve, and nothing may require itself."""
    for node in nodes.values():
        for prereq in node.prerequisites:
            if prereq == node.id:
                raise GraphCycleError(f"skill {node.id!r} lists itself as a prerequisite")
            if prereq not in nodes:
                raise DanglingPrerequisiteError(
                    f"skill {node.id!r} requires unknown prerequisite {prereq!r}"
                )


def build_graph(raw: list[dict[str, Any]]) -> SkillGraph:
    """Validate, index children, prove acyclicity, then precompute unlock counts."""
    nodes = _build_nodes(raw)
    _check_edges(nodes)

    children: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for node in nodes.values():
        for prereq in node.prerequisites:
            children[prereq].append(node.id)

    graph = SkillGraph(nodes=nodes, children={k: tuple(sorted(v)) for k, v in children.items()})
    graph.topological_sort()  # raises GraphCycleError when the relation is cyclic
    graph.unlocks = {node_id: len(graph.descendants(node_id)) for node_id in nodes}
    return graph


def load_graph_from(path: Path) -> SkillGraph:
    """Load a specific skills file. Used by tests with hand-built fixtures."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return build_graph(raw)


@lru_cache(maxsize=1)
def load_graph() -> SkillGraph:
    """Load and validate data/skills.json exactly once per process."""
    target = get_settings().data_dir / "skills.json"
    if not target.exists():
        logger.warning("skills.json not found at %s -- serving an empty graph", target)
        return SkillGraph(nodes={}, children={})
    graph = load_graph_from(target)
    logger.info("skill graph loaded: %d nodes across %d tracks", len(graph), len(graph.tracks))
    return graph


def reset_graph_cache() -> None:
    """Drop the cached graph. Used by tests that swap the data directory."""
    load_graph.cache_clear()
