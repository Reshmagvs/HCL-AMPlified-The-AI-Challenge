"""Deterministic profile extraction from free text.

This is the floor under the intake conversation. When the LLM is unavailable --
or when it returns something that fails schema validation twice -- intake still
has to produce a usable profile rather than a 500. So the same regex-and-keyword
extractor is used in two places: as the offline mock provider's answer, and as
the degraded fallback in the intake router.

It is deliberately conservative. Every rule below only fires on an explicit
statement ("6 hours a week", "free only"); anything not said stays ``None``,
because a fabricated field is worse than a missing one.
"""

from __future__ import annotations

import re
from typing import Any

_HOURS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*\+?\s*(?:hours?|hrs?|h)\b[^.]{0,20}?(?:per|a|each|/)\s*week", re.I
)
_HOURS_ALT_RE = re.compile(
    r"(?:per|a|each)\s*week[^.]{0,20}?(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", re.I
)
# "4 hours weekly" and "6 hrs/wk" are as common as "a week", and were missed.
_HOURS_WEEKLY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*\+?\s*(?:hours?|hrs?|h)\b\s*(?:weekly|/\s*wk|per\s*wk)", re.I
)
_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
_GOAL_RE = re.compile(
    r"(?:i\s+want\s+to\s+(?:be|become|learn|get\s+into|build|work)|"
    r"my\s+goal\s+is\s+to|i'?m\s+aiming\s+to|i\s+would\s+like\s+to|"
    r"help\s+me\s+(?:become|learn|get\s+into)|i\s+need\s+to\s+learn)\b(.{3,160})",
    re.I,
)
_COMPLETED_RE = re.compile(
    r"(?:i\s+(?:already\s+)?know|i'?ve\s+(?:done|used|learned|studied|completed)|"
    r"i\s+am\s+familiar\s+with|comfortable\s+with|experience\s+with|good\s+at)\b(.{3,120})",
    re.I,
)

# A captured clause ends where the learner starts a new thought.
_STOP_TAIL = re.compile(
    r"(?:,\s*|\s+)(?:but|so\s+i|because|since|though|however|and\s+i\s+\w+|i\s+want|"
    r"i'?m\s+aiming|my\s+goal|i'?ve|i\s+have|i\s+can|i\s+only|i\s+prefer)\b.*$",
    re.I,
)
# Fragments that describe a constraint, not a skill the learner claims to have.
_CONSTRAINT_RE = re.compile(r"\d|\bfree\b|\bhours?\b|\bweek\b|\bbudget\b|\bpaid\b", re.I)
_SPLIT_RE = re.compile(r",|\band\b|/|;", re.I)

_LEVEL_WORDS: dict[str, tuple[str, ...]] = {
    "advanced": ("advanced", "expert", "senior", "years of experience", "professional"),
    "intermediate": (
        "intermediate", "some experience", "a bit of experience", "comfortable with",
        "2nd year", "second year", "3rd year", "third year",
    ),
    "beginner": (
        "beginner", "complete beginner", "new to", "no experience", "just starting",
        "from scratch", "1st year", "first year",
    ),
}
_FORMAT_WORDS: dict[str, tuple[str, ...]] = {
    "video": ("video", "watch", "lectures"),
    "text": ("reading", "articles", "books", "written", "documentation", "text-based"),
    "interactive": ("interactive", "hands on", "hands-on", "exercises", "practice"),
}


def _clean(fragment: str) -> str:
    """Trim a captured fragment to a short, self-contained phrase."""
    text = _STOP_TAIL.sub("", fragment).strip(" .,!?;:-")
    return re.sub(r"\s+", " ", text)[:160]


def _clean_goal(fragment: str) -> str:
    """A goal stops at the first sentence or clause break; the rest is constraints."""
    text = _clean(fragment)
    for sep in (".", ",", ";", " with "):
        text = text.split(sep)[0]
    return text.strip(" .,!?;:-")[:160]


# A clause that states a constraint rather than a subject. Used to find where
# the goal ends when the learner did not wrap it in "I want to ...".
_CONSTRAINT_CLAUSE_RE = re.compile(
    r"^\s*(?:"
    r"\d+\s*(?:hours?|hrs?|h)\b"          # "6 hours a week"
    r"|(?:about|around|roughly)\s+\d+"     # "about 6 a week"
    r"|free\b|paid\b|cheap\b"
    r"|i\s|i'|my\s|and\s|but\s"
    r"|prefer|prefers|preferably"
    r"|beginner|intermediate|advanced"
    r"|video|text|interactive"
    r"|low\s+bandwidth|limited\s+data|slow\s+internet"
    r")",
    re.I,
)


# Words that carry no subject on their own. A clause built entirely from these
# is conversation, not a topic -- "hello there" must never become a goal.
_PLEASANTRIES = frozenset(
    """hi hello hey yo hiya greetings good morning afternoon evening night there
    thanks thank you please ok okay sure yes yeah yep no nope nah cool nice great
    sorry hmm um well so anyway right fine alright cheers bye help me my friend
    a an the is are was were be am do does did can could would should i you it
    what how why when where who this that these those and or but if to of for
    sounds looks seems works perfect awesome excellent got makes sense lets let
    start begin now again more much very too also just really on with""".split()
)


def _is_conversation(candidate: str) -> bool:
    """True when the clause says nothing a curriculum could be built from."""
    words = re.findall(r"[a-z0-9+#]+", candidate.lower())
    return not words or all(word in _PLEASANTRIES for word in words)


def _bare_subject(text: str) -> str | None:
    """The goal when the learner just names the subject.

    "I want to become an ML engineer" is caught by ``_GOAL_RE``. "organic
    chemistry for my class 12 board exam, 6 hours a week, free only" is not --
    it opens with the subject itself, which is how a great many people write.
    That sentence used to yield no goal at all, and the assistant then asked
    what the learner wanted to study, having just been told.

    So when no goal phrasing matches, the first clause is taken as the subject,
    provided it does not read as a constraint. Constraints ("6 hours a week",
    "free only", "I prefer video") are recognisable on their own and are exactly
    what the other extractors already handle, and so is conversation ("hello
    there"), which must not become a subject either.
    """
    for clause in re.split(r"[.,;]", text):
        candidate = _clean(clause)
        if len(candidate) < 3 or _CONSTRAINT_CLAUSE_RE.match(candidate):
            continue
        if _is_conversation(candidate):
            continue
        words = candidate.split()
        if not 1 <= len(words) <= 14:
            continue
        return candidate.strip(" .,!?;:-")[:160]
    return None


def _find_hours(text: str) -> float | None:
    for pattern in (_HOURS_RE, _HOURS_ALT_RE, _HOURS_WEEKLY_RE):
        match = pattern.search(text)
        if match:
            hours = float(match.group(1))
            if 0 < hours <= 80:
                return hours
    return None


def _find_level(low: str) -> str | None:
    for level, words in _LEVEL_WORDS.items():
        if any(word in low for word in words):
            return level
    return None


def _find_format(low: str) -> str | None:
    hits = [fmt for fmt, words in _FORMAT_WORDS.items() if any(w in low for w in words)]
    return hits[0] if len(hits) == 1 else None


def _find_completed(text: str) -> list[str]:
    """Pull claimed prior knowledge out of "I already know X, Y and Z" phrasing."""
    found: list[str] = []
    for match in _COMPLETED_RE.finditer(text):
        for part in _SPLIT_RE.split(_clean(match.group(1))):
            item = part.strip(" .,!?;:-")
            if 1 < len(item) <= 40 and not _CONSTRAINT_RE.search(item):
                found.append(item)
    seen: set[str] = set()
    return [x for x in found if not (x.lower() in seen or seen.add(x.lower()))][:8]


def extract_profile(text: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge whatever ``text`` explicitly states into ``existing``.

    Existing values win unless a field is currently unset -- intake is additive,
    and a later message must not blank out something already established.
    """
    profile: dict[str, Any] = dict(existing or {})
    low = text.lower()

    updates: dict[str, Any] = {
        "hours_per_week": _find_hours(text),
        "experience_level": _find_level(low),
        "format_pref": _find_format(low),
    }

    goal_match = _GOAL_RE.search(text)
    if goal_match:
        updates["goal_text"] = _clean_goal(goal_match.group(1))
    elif not profile.get("goal_text"):
        # No goal phrasing, and none established yet: the learner probably just
        # named the subject.
        subject = _bare_subject(text)
        if subject:
            updates["goal_text"] = subject

    if any(w in low for w in ("free only", "only free", "free resources", "can't pay",
                              "cannot pay", "no money", "tight budget", "no budget")):
        updates["cost_pref"] = "free"
    if any(w in low for w in ("low bandwidth", "limited data", "slow internet",
                              "data plan", "mobile data")):
        updates["low_bandwidth"] = True
        updates.setdefault("format_pref", "text")

    date_match = _DATE_RE.search(text)
    if date_match:
        updates["target_date"] = date_match.group(0)

    completed = _find_completed(text)
    if completed:
        updates["completed_skills"] = list(
            dict.fromkeys([*(profile.get("completed_skills") or []), *completed])
        )

    for key, value in updates.items():
        if value is not None and profile.get(key) in (None, "", [], "any"):
            profile[key] = value
    return profile


# The order fields are asked for, most load-bearing first. A plan cannot be
# built without the first two; the rest only shape it.
FIELD_ORDER = ("goal_text", "hours_per_week", "experience_level", "cost_pref")

# Each field gets several phrasings, tried in order. Repeating a question
# verbatim is what a form does, not what a person does -- and when the first
# phrasing did not land, saying exactly the same words again is the least
# likely thing to help. Later attempts get more concrete, ending in an example
# the learner can copy.
_PHRASINGS: dict[str, tuple[str, ...]] = {
    "goal_text": (
        "What would you like to be able to do? Describe the goal in your own words.",
        "I did not catch a subject in that. What is it you want to learn or be able to do?",
        "Tell me just the subject and I will take it from there -- for example "
        "\"business studies\", \"organic chemistry\" or \"become a data analyst\".",
    ),
    "hours_per_week": (
        "Roughly how many hours a week can you give this?",
        "How much time do you have for this each week? A rough number is fine.",
        "Give me a number of hours a week -- for example \"about 5 hours a week\".",
    ),
    "experience_level": (
        "How would you describe your current level: beginner, intermediate or advanced?",
        "Are you starting from scratch with this, or do you already know some of it?",
        "Just pick the closest one: beginner, intermediate or advanced.",
    ),
    "cost_pref": (
        "Should I stick to free resources only, or is paid material fine too?",
        "Do you want free material only, or is it fine to include paid courses?",
        "Reply \"free\" for free-only, or \"any\" to include paid material.",
    ),
}

# Said once the profile is complete. Varied for the same reason as the
# questions: a learner who keeps typing should not get one sentence on a loop.
_READY_LINES = (
    "That is everything I need -- ready to build your path.",
    "Noted. You have everything needed for a plan whenever you are ready.",
    "Got it. Press \"Build my plan\" whenever you want to start.",
)


def missing_field(profile: dict[str, Any]) -> str | None:
    """The next field worth asking for, or None when the profile is complete."""
    for field in FIELD_ORDER:
        value = profile.get(field)
        if field == "cost_pref":
            if value in (None, "", "any"):
                return field
        elif not value:
            return field
    return None


def next_question(profile: dict[str, Any], attempt: int = 0) -> str:
    """The single most useful thing still missing, phrased as a question.

    ``attempt`` is how many times this same field has already been asked for.
    It selects a different phrasing rather than repeating one -- the defect
    this argument exists to fix was a learner being shown the identical
    sentence on every turn because the rules could not read their answer and
    had no memory that they had already asked.
    """
    field = missing_field(profile)
    if field is None:
        return _READY_LINES[min(attempt, len(_READY_LINES) - 1)]

    phrasings = _PHRASINGS[field]
    question = phrasings[min(attempt, len(phrasings) - 1)]

    # The first time we can name the goal back, do -- it shows the goal landed.
    if field == "hours_per_week" and attempt == 0 and profile.get("goal_text"):
        return f"Got it -- {profile['goal_text']}. {question}"
    return question
