"""SQLModel tables -- the persistent state of one learner's journey.

The shape here follows one rule: **nothing mutates silently**. A learning path
is never edited in place; adaptation writes a new ``LearningPath`` version and
supersedes the old one, so any two versions can be diffed after the fact. Every
state change also appends an ``Event`` row, which is simultaneously the audit
log, the adaptation trigger record and the dashboard's activity feed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    """Timezone-aware UTC timestamp (``datetime.utcnow`` is deprecated)."""
    return datetime.now(timezone.utc)


class Learner(SQLModel, table=True):
    """One learner profile. Created by ``POST /api/intake/commit``."""

    id: int | None = Field(default=None, primary_key=True)
    display_name: str = "Learner"
    goal_text: str = ""
    goal_node_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    interests: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    experience_level: str = "beginner"
    hours_per_week: float = 6.0
    target_date: str | None = None
    format_pref: str = "any"       # video | text | interactive | any
    cost_pref: str = "any"         # free | any
    language: str = "en"
    low_bandwidth: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class Mastery(SQLModel, table=True):
    """Estimated command of one skill, 0..1, with the evidence that produced it.

    ``source`` follows a strict precedence -- ``milestone > diagnostic > self``.
    A weaker source may never overwrite a stronger one, which is what stops a
    self-report from erasing a measured result.
    """

    __table_args__ = (UniqueConstraint("learner_id", "skill_id", name="uq_mastery"),)

    id: int | None = Field(default=None, primary_key=True)
    learner_id: int = Field(foreign_key="learner.id", index=True)
    skill_id: str = Field(index=True)
    score: float = 0.0
    source: str = "self"           # self | diagnostic | milestone
    confidence: float = 0.0
    updated_at: datetime = Field(default_factory=_utcnow)


class LearningPath(SQLModel, table=True):
    """One immutable version of a learner's plan."""

    id: int | None = Field(default=None, primary_key=True)
    learner_id: int = Field(foreign_key="learner.id", index=True)
    version: int = 1
    status: str = "active"         # active | superseded
    total_hours: float = 0.0
    finish_week: int = 0
    llm_degraded: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class PathItem(SQLModel, table=True):
    """One step: a skill, the resource bound to it, and why both were chosen."""

    id: int | None = Field(default=None, primary_key=True)
    path_id: int = Field(foreign_key="learningpath.id", index=True)
    order_index: int = 0
    week_number: int = 1
    skill_id: str = Field(index=True)
    course_id: str | None = None
    kind: str = "resource"         # resource | milestone
    status: str = "pending"        # pending | in_progress | done | skipped
    est_hours: float = 0.0
    provenance: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    rationale_text: str = ""
    alternatives: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class Event(SQLModel, table=True):
    """Append-only audit log. Powers adaptation, the feed and the dashboard."""

    id: int | None = Field(default=None, primary_key=True)
    learner_id: int = Field(foreign_key="learner.id", index=True)
    type: str = Field(index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)


class QuizItem(SQLModel, table=True):
    """A diagnostic or milestone question. The answer key never leaves the server."""

    id: int | None = Field(default=None, primary_key=True)
    learner_id: int = Field(foreign_key="learner.id", index=True)
    skill_id: str = Field(index=True)
    kind: str = "diagnostic"       # diagnostic | milestone
    question: str = ""
    options: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    answer_index: int = 0
    chosen_index: int | None = None
    dont_know: bool = False
    correct: bool | None = None
    asked_at: datetime = Field(default_factory=_utcnow)


class EmbeddingCache(SQLModel, table=True):
    """SHA256(text) -> vector, so identical text is never embedded twice."""

    text_hash: str = Field(primary_key=True)
    model: str = ""
    vector: list[float] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)


class IntakeSession(SQLModel, table=True):
    """Conversation state during intake, before a ``Learner`` row exists."""

    id: str = Field(primary_key=True)
    transcript: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    profile: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    learner_id: int | None = None
    created_at: datetime = Field(default_factory=_utcnow)
