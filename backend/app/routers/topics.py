"""Topics: checking whether a goal is understood, and building it if not.

Three endpoints, and the split between them is the whole design.

``GET /api/topics/coverage`` is cheap and synchronous -- about a third of a
second, no model. It answers "do we already teach this", and it is what the
intake screen calls while the learner is still typing, so the answer is on
screen before they finish filling in their hours.

``POST /api/topics/build`` starts a build and returns immediately. Building a
subject takes a couple of minutes on a CPU; holding a request open for that is
how you collect proxy timeouts and abandoned tabs.

``GET /api/topics/build`` reports progress in words meant for a learner --
which skill is being searched for, how many resources have been verified.

The override matters as much as the automation. Coverage is a judgement, and it
is wrong sometimes; ``force`` lets a learner who was told "this looks like
Algebra and Notation" say *no, build it properly* and get their subject. A
classifier a person can correct is worth more than one that is slightly more
accurate and final.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core import expansion, jobs, store, websearch
from app.core.skill_graph import load_graph
from app.llm import get_provider

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/topics", tags=["topics"])

MAX_GOAL_CHARS = 300


class CoverageResponse(BaseModel):
    """What the curriculum makes of a goal, and what it would do about it."""

    goal_text: str
    covered: bool
    matched_skill_id: str | None = None
    matched_skill_name: str | None = None
    reason: str
    already_built: bool = False
    topic: str | None = None
    can_build: bool = True
    build_unavailable_reason: str = ""


class BuildRequest(BaseModel):
    goal_text: str = Field(min_length=2, max_length=MAX_GOAL_CHARS)
    # Build even though coverage says the curriculum already handles this.
    force: bool = False


class BuildStatus(BaseModel):
    goal_text: str
    status: str
    stage: str = ""
    detail: str = ""
    progress: float = 0.0
    topic: str = ""
    goal_skill_ids: list[str] = Field(default_factory=list)
    skill_count: int = 0
    resource_count: int = 0
    error: str = ""
    elapsed: float = 0.0


def _build_availability() -> tuple[bool, str]:
    """Whether a new topic could be built right now, and why not if it cannot."""
    if not get_settings().expansion_enabled:
        return False, "Building new subjects is switched off in this deployment."
    provider = get_provider()
    if provider.name == "mock" or not provider.available():
        return False, (
            "Building a new subject needs a local language model. Install Ollama "
            "and run: ollama pull " + get_settings().ollama_model
        )
    return True, ""


@router.get("/coverage", response_model=CoverageResponse)
def check_coverage(goal_text: str = Query(min_length=2, max_length=MAX_GOAL_CHARS)) -> CoverageResponse:
    """Does the curriculum already teach this? Fast enough to call while typing."""
    built = store.find_topic(goal_text)
    if built:
        return CoverageResponse(
            goal_text=goal_text,
            covered=True,
            reason=f"{built['topic']} was built for this goal already",
            already_built=True,
            topic=built["topic"],
            can_build=True,
        )

    verdict = expansion.assess_coverage(goal_text)
    graph = load_graph()
    can_build, why_not = _build_availability()
    matched = verdict.best_id if verdict.covered else None

    return CoverageResponse(
        goal_text=goal_text,
        covered=verdict.covered,
        matched_skill_id=matched,
        matched_skill_name=graph.require(matched).name if matched and matched in graph else None,
        reason=verdict.reason,
        can_build=can_build,
        build_unavailable_reason=why_not,
    )


@router.post("/build", response_model=BuildStatus)
def start_build(payload: BuildRequest) -> BuildStatus:
    """Begin building a subject, or join the build already running for it."""
    can_build, why_not = _build_availability()
    if not can_build:
        raise HTTPException(status_code=503, detail=why_not)

    built = store.find_topic(payload.goal_text)
    if built and not payload.force:
        graph = load_graph()
        goals = [g for g in built.get("goal_skill_ids", []) if g in graph]
        if goals:
            return BuildStatus(
                goal_text=payload.goal_text,
                status="done",
                stage="Ready",
                detail="already built",
                progress=1.0,
                topic=built["topic"],
                goal_skill_ids=goals,
                skill_count=len(built.get("skill_ids", [])),
                resource_count=len(built.get("course_ids", [])),
            )

    job = jobs.start(payload.goal_text)
    return BuildStatus(goal_text=payload.goal_text, **job.as_dict())


@router.get("/build", response_model=BuildStatus)
def build_status(goal_text: str = Query(min_length=2, max_length=MAX_GOAL_CHARS)) -> BuildStatus:
    """Progress for a build. Reports a finished topic even after its job is swept."""
    job = jobs.get(goal_text)
    if job:
        return BuildStatus(goal_text=goal_text, **job.as_dict())

    built = store.find_topic(goal_text)
    if built:
        graph = load_graph()
        return BuildStatus(
            goal_text=goal_text,
            status="done",
            stage="Ready",
            detail=f"{len(built.get('skill_ids', []))} skills",
            progress=1.0,
            topic=built["topic"],
            goal_skill_ids=[g for g in built.get("goal_skill_ids", []) if g in graph],
            skill_count=len(built.get("skill_ids", [])),
            resource_count=len(built.get("course_ids", [])),
        )
    return BuildStatus(goal_text=goal_text, status="none", stage="", progress=0.0)


@router.get("/sources")
def discovery_sources() -> dict[str, object]:
    """Which discovery sources are answering. Useful when a build finds nothing."""
    return {"sources": websearch.source_health()}
