"""Provenance: the reason for a recommendation, built as data before it is prose.

The weak version of "explain why" is asking a model to write a paragraph about a
recommendation. It sounds plausible and proves nothing, because nothing in the
paragraph is checkable.

This module does the opposite. Every claim a learner will read is computed here
first, from the graph, the mastery table and the scoring components, into a
structured record::

    {"skill": ...,
     "why_needed":        {"goal": ..., "path_to_goal": [...]},
     "your_level":        {"score": ..., "source": ..., "evidence_q_ids": [...]},
     "why_this_resource": {"beat_alternatives": N, "reasons": [...]},
     "placement":         {"week": N, "unlocks": [...]}}

Only that object is handed to the model, with no other context, so it cannot
state a reason the data does not support -- a hallucinated justification becomes
structurally impossible rather than merely unlikely. And because the record is
complete on its own, ``render_template`` can produce the same two sentences with
no model at all. **The reason always exists.**

``path_to_goal`` is the shortest chain of dependents from this skill up to a
goal node: the concrete answer to "why am I being asked to learn this?"
"""

from __future__ import annotations

from collections import deque
from typing import Any

from app.core.retrieval import Preferences, Resource, ScoredResource
from app.core.skill_graph import SkillGraph


def shortest_path_to_goal(graph: SkillGraph, skill_id: str, goal_ids: list[str]) -> list[str]:
    """Breadth-first walk *down* the dependency edges to the nearest goal.

    Returns the intermediate skills between ``skill_id`` and the goal, goal
    included, excluding ``skill_id`` itself. Empty when the skill *is* a goal.
    """
    if skill_id in goal_ids:
        return []
    goals = set(goal_ids)
    previous: dict[str, str] = {skill_id: ""}
    queue: deque[str] = deque([skill_id])

    while queue:
        current = queue.popleft()
        for child in graph.children.get(current, ()):
            if child in previous:
                continue
            previous[child] = current
            if child in goals:
                chain = [child]
                while previous[chain[-1]] and chain[-1] != skill_id:
                    chain.append(previous[chain[-1]])
                return list(reversed(chain[:-1]))
            queue.append(child)
    return []


def _resource_reasons(
    scored: ScoredResource, prefs: Preferences, hours_per_week: float
) -> list[str]:
    """Turn score components into short factual phrases, strongest first."""
    resource = scored.resource
    reasons: list[str] = []

    if resource.cost == "free":
        reasons.append("free to access")
    if prefs.format_pref not in ("", "any") and resource.format == prefs.format_pref:
        reasons.append(f"{resource.format} matches your stated preference")
    elif scored.components.get("format", 0) >= 0.6:
        reasons.append(f"{resource.format} format")
    if scored.components.get("level", 0) >= 1.0:
        reasons.append(f"pitched at {resource.level} level, which fits this skill")
    if resource.duration_hours <= hours_per_week:
        reasons.append(
            f"{resource.duration_hours:g}h fits inside your {hours_per_week:g}h week"
        )
    if resource.rating >= 4.5:
        reasons.append(f"rated {resource.rating:g}")
    if prefs.low_bandwidth and resource.format != "video":
        reasons.append("light on bandwidth")
    return reasons[:4]


def build_provenance(
    *,
    graph: SkillGraph,
    skill_id: str,
    goal_ids: list[str],
    goal_label: str,
    mastery_score: float,
    mastery_source: str,
    evidence_q_ids: list[int],
    ranked: list[ScoredResource],
    week: int,
    prefs: Preferences,
    hours_per_week: float,
) -> dict[str, Any]:
    """Assemble the complete, self-contained reason for one path item."""
    node = graph.require(skill_id)
    chosen = ranked[0] if ranked else None

    return {
        "skill": skill_id,
        "skill_name": node.name,
        "track": node.track,
        "why_needed": {
            "goal": goal_label,
            "path_to_goal": shortest_path_to_goal(graph, skill_id, goal_ids),
            "is_goal": skill_id in goal_ids,
        },
        "your_level": {
            "score": round(mastery_score, 3),
            "source": mastery_source,
            "evidence_q_ids": evidence_q_ids,
            "threshold": 0.7,
        },
        "why_this_resource": {
            "resource_id": chosen.resource.id if chosen else None,
            "title": chosen.resource.title if chosen else None,
            "provider": chosen.resource.provider if chosen else None,
            "beat_alternatives": max(0, len(ranked) - 1),
            "score": round(chosen.score, 4) if chosen else None,
            "reasons": _resource_reasons(chosen, prefs, hours_per_week) if chosen else [],
        },
        "placement": {
            "week": week,
            "est_hours": node.est_hours,
            "unlocks": sorted(graph.children.get(skill_id, ()))[:6],
            "unlock_count": graph.downstream_unlock_count(skill_id),
        },
    }


def _level_phrase(level: dict[str, Any]) -> str:
    """How to describe the learner's current standing, honestly."""
    score, source = level["score"], level["source"]
    if source == "unmeasured":
        return "we have not measured this yet"
    if source == "self":
        return f"you rated yourself around {score:.0%} here"
    verb = "your diagnostic" if source == "diagnostic" else "your milestone check"
    return f"{verb} put you at {score:.0%}"


def render_template(provenance: dict[str, Any]) -> str:
    """The deterministic two-sentence rendering of a provenance record.

    Used verbatim when the provider is unavailable, and as the mock provider's
    answer -- so the offline experience is the real experience, minus polish.
    """
    why = provenance["why_needed"]
    level = provenance["your_level"]
    resource = provenance["why_this_resource"]
    placement = provenance["placement"]
    name = provenance.get("skill_name", provenance["skill"])

    if why.get("is_goal"):
        first = f"{name} is your goal itself, and {_level_phrase(level)}."
    elif why["path_to_goal"]:
        chain = " then ".join(why["path_to_goal"][:3])
        first = (
            f"{name} is needed for {why['goal']} because it leads into {chain}, "
            f"and {_level_phrase(level)}."
        )
    else:
        first = f"{name} is part of what {why['goal']} requires, and {_level_phrase(level)}."

    if not resource.get("title"):
        second = (
            f"It sits in week {placement['week']}; no resource in the catalog covers it yet, "
            "so treat this as self-study."
        )
    else:
        reasons = resource["reasons"]
        because = (
            ", ".join(reasons[:2]) if reasons else "it scored highest against your preferences"
        )
        beat = resource["beat_alternatives"]
        alternatives = f" over {beat} alternative{'s' if beat != 1 else ''}" if beat else ""
        unlocks = placement["unlock_count"]
        opens = f" and opens up {unlocks} later skill{'s' if unlocks != 1 else ''}" if unlocks else ""
        second = (
            f"{resource['title']} was chosen{alternatives} because it is {because}, "
            f"and it lands in week {placement['week']}{opens}."
        )
    return f"{first} {second}"


def resource_payload(resource: Resource) -> dict[str, Any]:
    """The catalog fields the frontend renders on a card."""
    return resource.as_dict()
