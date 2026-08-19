"""Print a human review of the skill graph.

No test can catch a prerequisite edge that is structurally valid but
pedagogically wrong -- only a person reading the graph can. This report surfaces
the two views that make such an error visible: how deep each track goes (a flat
track means the sequencing has nothing to sequence) and which nodes gate the
most downstream work (a bottleneck in the wrong place distorts every path).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.skill_graph import SkillGraph, load_graph  # noqa: E402


def _track_table(graph: SkillGraph) -> list[str]:
    rows = ["  track              nodes  max-depth  levels  deepest node"]
    for track in graph.tracks:
        nodes = graph.by_track(track)
        depths = {n.id: graph.depth(n.id) for n in nodes}
        deepest = max(depths, key=lambda k: (depths[k], k))
        rows.append(
            f"  {track:<18} {len(nodes):>5}  {depths[deepest]:>9}  "
            f"{len(set(depths.values())):>6}  {deepest}"
        )
    return rows


def _unlock_table(graph: SkillGraph, limit: int = 5) -> list[str]:
    ranked = sorted(
        graph.nodes.values(),
        key=lambda n: (-graph.downstream_unlock_count(n.id), n.id),
    )[:limit]
    rows = ["  unlocks  node"]
    for node in ranked:
        rows.append(f"  {graph.downstream_unlock_count(node.id):>7}  {node.id} ({node.name})")
    return rows


def _terminal_goals(graph: SkillGraph) -> list[str]:
    """Nodes nothing depends on -- the goals a learner can actually aim at."""
    leaves = [n for n in graph.nodes.values() if not graph.children.get(n.id)]
    return [
        f"  {n.id:<28} {len(graph.ancestors_closure([n.id])):>3} required  {n.name}"
        for n in sorted(leaves, key=lambda n: n.id)
    ]


def main() -> int:
    graph = load_graph()
    if not graph:
        print("skills.json is missing -- run: python -m scripts.build_skills")
        return 1

    edges = sum(len(n.prerequisites) for n in graph.nodes.values())
    difficulty = Counter(n.difficulty for n in graph.nodes.values())

    print()
    print("SKILL GRAPH REVIEW")
    print("=" * 72)
    print(f"  {len(graph)} nodes, {edges} prerequisite edges, {len(graph.tracks)} tracks")
    print(f"  total estimated hours: {sum(n.est_hours for n in graph.nodes.values()):.0f}")
    print(f"  difficulty spread: {dict(sorted(difficulty.items()))}")
    print()
    print("DEPTH PER TRACK")
    print("\n".join(_track_table(graph)))
    print()
    print("HIGHEST DOWNSTREAM UNLOCK COUNT")
    print("\n".join(_unlock_table(graph)))
    print()
    print("TERMINAL GOAL NODES (nothing depends on these)")
    print("\n".join(_terminal_goals(graph)))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
