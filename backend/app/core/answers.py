"""Rule-based answers about a learner's own plan, with no language model.

The offline provider used to answer chat questions by echoing the context block
it had been handed, which read like a debug dump. That is the *default*
experience — most installs have no model configured — so it had to be better
than a fallback that merely avoids crashing.

Everything a learner actually asks about their plan is a lookup, not a
generation task: what do I do next, how long is left, why is this here, what
have I finished. So those are answered from the rows directly, in full
sentences, with the same numbers the rest of the interface shows. A language
model, when present, makes the phrasing better; it does not make the answers
more correct.

Only genuinely open-ended questions fall through to a summary that says plainly
what this assistant can and cannot answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

WHY_RE = re.compile(r"\bwhy\b", re.I)
NEXT_RE = re.compile(r"\b(next|first|start|begin|now|today)\b", re.I)
TIME_RE = re.compile(r"\b(how long|hours?|weeks?|finish|done by|time left|remaining)\b", re.I)
PROGRESS_RE = re.compile(r"\b(progress|how far|completed|finished|left to do)\b", re.I)
COST_RE = re.compile(r"\b(free|cost|pay|paid|price)\b", re.I)


@dataclass(frozen=True)
class PlanFacts:
    """The subset of a learner's plan these answers are drawn from."""

    goal: str
    hours_per_week: float
    finish_week: int
    total_steps: int
    done_steps: int
    hours_done: float
    hours_remaining: float
    upcoming: list[dict[str, Any]]   # skill_name, week, title, url, chain
    all_steps: list[dict[str, Any]]  # skill_name, skill_id, week, title, status
    paid_count: int


def _listed(steps: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"week {s['week']}, {s['skill_name']}"
        + (f" via {s['title']}" if s.get("title") else "")
        for s in steps
    )


def _find_step(facts: PlanFacts, question: str) -> dict[str, Any] | None:
    """Match a question against a step by name, longest name first.

    Longest-first matters: "regression" should not win over "logistic
    regression" when both are in the plan and the learner named the latter.
    """
    lowered = question.lower()
    for step in sorted(facts.all_steps, key=lambda s: -len(s["skill_name"])):
        if step["skill_name"].lower() in lowered:
            return step
    return None


def answer(question: str, facts: PlanFacts) -> str:
    """Answer from the plan, or say plainly that it is outside what we know."""
    if not facts.all_steps:
        return (
            "You do not have a plan yet. Describe your goal and take the short "
            "placement check, and I will be able to answer questions about it."
        )

    named = _find_step(facts, question)

    if WHY_RE.search(question) and named:
        return _why(named, facts)
    if TIME_RE.search(question):
        return _timing(facts)
    if PROGRESS_RE.search(question):
        return _progress(facts)
    if COST_RE.search(question):
        return _cost(facts)
    if NEXT_RE.search(question) or not named:
        return _next(facts)
    return _about(named, facts)


def _next(facts: PlanFacts) -> str:
    if not facts.upcoming:
        return (
            f"Every step in your plan is complete — all {facts.total_steps} of them. "
            "Change your goal to keep going."
        )
    first = facts.upcoming[0]
    rest = facts.upcoming[1:3]
    reply = f"Start with {first['skill_name']} in week {first['week']}"
    if first.get("title"):
        reply += f", using {first['title']}"
    reply += "."
    if rest:
        reply += f" After that: {_listed(rest)}."
    reply += (
        f" You have {facts.hours_remaining:g} hours left overall, "
        f"finishing in week {facts.finish_week}."
    )
    return reply


def _why(step: dict[str, Any], facts: PlanFacts) -> str:
    chain = step.get("chain") or []
    if chain:
        route = " → ".join(chain[:3])
        return (
            f"{step['skill_name']} is in your plan because {facts.goal} depends on it: "
            f"it leads into {route}. It sits in week {step['week']}"
            + (f", covered by {step['title']}" if step.get("title") else "")
            + ". Open “Why this, and why now?” on that step for the full trace."
        )
    return (
        f"{step['skill_name']} is part of what {facts.goal} requires directly, scheduled for "
        f"week {step['week']}."
    )


def _about(step: dict[str, Any], facts: PlanFacts) -> str:
    status = {"done": "already marked done", "pending": "still to do"}.get(
        step.get("status", "pending"), step.get("status", "")
    )
    return (
        f"{step['skill_name']} is in week {step['week']} of your plan and is {status}"
        + (f", using {step['title']}" if step.get("title") else "")
        + f". Your goal is {facts.goal}, finishing in week {facts.finish_week}."
    )


def _timing(facts: PlanFacts) -> str:
    return (
        f"At {facts.hours_per_week:g} hours a week you finish in week {facts.finish_week}. "
        f"You have done {facts.hours_done:g} hours and have {facts.hours_remaining:g} left "
        f"across {facts.total_steps - facts.done_steps} remaining step"
        f"{'s' if facts.total_steps - facts.done_steps != 1 else ''}. "
        "The slider on the plan screen shows what a different weekly commitment would do."
    )


def _progress(facts: PlanFacts) -> str:
    percent = round(100 * facts.done_steps / max(1, facts.total_steps))
    reply = (
        f"You have completed {facts.done_steps} of {facts.total_steps} steps ({percent}%), "
        f"which is {facts.hours_done:g} hours of study."
    )
    if facts.upcoming:
        reply += f" Next up is {facts.upcoming[0]['skill_name']} in week {facts.upcoming[0]['week']}."
    return reply


def _cost(facts: PlanFacts) -> str:
    if facts.paid_count == 0:
        return (
            f"Every resource in your plan is free — all {facts.total_steps} steps. "
            "Nothing in it needs a subscription or a purchase."
        )
    return (
        f"{facts.paid_count} of your {facts.total_steps} steps use a paid resource. "
        "Set your preference to free-only and rebuild the plan to replace them."
    )
