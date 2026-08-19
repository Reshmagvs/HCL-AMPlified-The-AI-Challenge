"""Wire contracts.

Every request body and response body crossing the HTTP boundary is declared
here so the frontend's generated types and the backend's validation come from a
single description. LLM-shaped payloads additionally reuse these models as
JSON-schema targets -- see ``app.llm.base.call_with_schema``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    version: str
    llm_available: bool
    llm_provider: str
    catalog_size: int
    graph_nodes: int
    graph_tracks: int


# --------------------------------------------------------------------------- #
# Intake
# --------------------------------------------------------------------------- #
class ProfileDraft(BaseModel):
    """The structured profile the LLM extracts from free-form conversation.

    Every field is optional: extraction is incremental, and a field that was
    never stated must stay ``None`` rather than being invented.
    """

    interests: list[str] | None = None
    experience_level: Literal["beginner", "intermediate", "advanced"] | None = None
    completed_skills: list[str] | None = None
    goal_text: str | None = None
    hours_per_week: float | None = None
    target_date: str | None = None
    format_pref: Literal["video", "text", "interactive", "any"] | None = None
    cost_pref: Literal["free", "any"] | None = None
    language: str | None = None
    low_bandwidth: bool | None = None


class IntakeMessageRequest(BaseModel):
    session_id: str | None = None
    message: str


class IntakeMessageResponse(BaseModel):
    session_id: str
    assistant_message: str
    profile: ProfileDraft
    ready: bool
    llm_degraded: bool = False


class IntakeCommitRequest(BaseModel):
    session_id: str | None = None
    profile: ProfileDraft | None = None
    display_name: str = "Learner"


class GoalCandidate(BaseModel):
    skill_id: str
    name: str
    track: str
    score: float


class IntakeCommitResponse(BaseModel):
    learner_id: int
    goal_node_ids: list[str]
    goal_names: list[str]
    candidates: list[GoalCandidate]
    seeded_mastery: dict[str, float]
    llm_degraded: bool = False


# --------------------------------------------------------------------------- #
# Diagnostic
# --------------------------------------------------------------------------- #
class DiagnosticQuestion(BaseModel):
    """Deliberately carries no ``answer_index`` -- the key stays server-side."""

    done: bool = False
    quiz_item_id: int | None = None
    skill_id: str | None = None
    skill_name: str | None = None
    question: str | None = None
    options: list[str] = Field(default_factory=list)
    asked: int = 0
    max_questions: int = 10
    confidence: float = 0.0
    llm_degraded: bool = False


class DiagnosticAnswerRequest(BaseModel):
    quiz_item_id: int
    chosen_index: int | None = None
    dont_know: bool = False


class DiagnosticAnswerResponse(BaseModel):
    correct: bool
    skill_id: str
    new_score: float
    confidence: float
    asked: int
    done: bool


# --------------------------------------------------------------------------- #
# Path
# --------------------------------------------------------------------------- #
class ResourceOut(BaseModel):
    id: str
    title: str
    provider: str
    url: str
    format: str
    cost: str
    duration_hours: float
    level: str
    rating: float
    description: str = ""


class PathItemOut(BaseModel):
    id: int | None = None
    order_index: int
    week_number: int
    skill_id: str
    skill_name: str
    kind: str
    status: str
    est_hours: float
    course: ResourceOut | None = None
    alternatives: list[ResourceOut] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    rationale_text: str = ""


class PathResponse(BaseModel):
    learner_id: int
    path_id: int | None
    version: int
    status: str
    total_hours: float
    finish_week: int
    hours_per_week: float
    goal_node_ids: list[str]
    goal_names: list[str]
    items: list[PathItemOut] = Field(default_factory=list)
    llm_degraded: bool = False


class WhatIfRequest(BaseModel):
    learner_id: int
    hours_per_week: float = Field(gt=0, le=80)
    cost_pref: Literal["free", "any"] | None = None
    format_pref: Literal["video", "text", "interactive", "any"] | None = None


class WhatIfResponse(BaseModel):
    hours_per_week: float
    finish_week: int
    total_hours: float
    item_count: int
    weeks: list[dict[str, Any]] = Field(default_factory=list)
    persisted: bool = False


# --------------------------------------------------------------------------- #
# Adaptation
# --------------------------------------------------------------------------- #
EventType = Literal[
    "milestone_failed",
    "too_easy",
    "too_hard",
    "behind_schedule",
    "goal_changed",
    "resource_disliked",
    "completed_item",
]


class PathEventRequest(BaseModel):
    learner_id: int
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)


class PathDiff(BaseModel):
    from_version: int
    to_version: int
    added: list[dict[str, Any]] = Field(default_factory=list)
    removed: list[dict[str, Any]] = Field(default_factory=list)
    moved_weeks: list[dict[str, Any]] = Field(default_factory=list)
    resource_swapped: list[dict[str, Any]] = Field(default_factory=list)
    finish_week_delta: int = 0
    unchanged: bool = True


class PathEventResponse(BaseModel):
    event: str
    message: str
    version: int
    diff: PathDiff
    options: list[str] = Field(default_factory=list)
    llm_degraded: bool = False


# --------------------------------------------------------------------------- #
# Chat / dashboard / graph
# --------------------------------------------------------------------------- #
class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    citations: list[dict[str, str]] = Field(default_factory=list)
    llm_degraded: bool = False


class DashboardResponse(BaseModel):
    learner_id: int
    goal_names: list[str]
    items_total: int
    items_done: int
    progress_pct: float
    hours_done: float
    hours_remaining: float
    finish_week: int
    current_week: int
    mastery_radar: list[dict[str, Any]] = Field(default_factory=list)
    milestones: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[PathItemOut] = Field(default_factory=list)
    activity: list[dict[str, Any]] = Field(default_factory=list)


class GraphNodeOut(BaseModel):
    id: str
    name: str
    track: str
    difficulty: int
    est_hours: float
    mastery: float
    source: str | None = None
    in_path: bool = False
    is_goal: bool = False
    week: int | None = None


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    in_path: bool = False


class GraphResponse(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
