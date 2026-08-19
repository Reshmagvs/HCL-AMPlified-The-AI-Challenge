"""Adaptive placement: measure the learner instead of trusting the self-report.

The brief only asks the system to *capture* an experience level. Self-report is
the weakest signal in education technology, so this router measures instead, and
it does so adaptively.

**Item selection maximises `uncertainty x downstream_unlock_count`.** Asking
about a leaf skill resolves one unknown; asking about a skill that gates twelve
others resolves the shape of the whole path. Weighting by unlock count is what
lets six questions do the work of twenty.

**The answer key never leaves the server.** `QuizItem.answer_index` is written
to the database and graded server-side. No response body in this module contains
it, and a test asserts that across the whole payload.

**"I don't know" is a first-class answer.** It is recorded distinctly from a
wrong answer, because a guess that happens to be wrong and an honest abstention
say different things about the learner. Guessing pollutes the measurement.

Termination is guaranteed: the loop stops on sufficient confidence, on running
out of assessable gap skills, or at `DIAGNOSTIC_MAX_QUESTIONS`, whichever comes
first.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.config import get_settings
from app.core.mastery import MasteryTable, MasteryValue, apply_answer, confidence
from app.core.skill_graph import SkillGraph, SkillNode, load_graph
from app.db import get_session
from app.llm import get_provider
from app.llm.base import ProviderUnavailable, SchemaViolation, call_with_schema
from app.llm.mock import MockProvider
from app.llm.prompts import QUIZ_GENERATION
from app.models import Event, Learner, Mastery, QuizItem
from app.schemas import (
    DiagnosticAnswerRequest,
    DiagnosticAnswerResponse,
    DiagnosticQuestion,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/diagnostic", tags=["diagnostic"])


class GeneratedQuestion(BaseModel):
    question: str = Field(min_length=8, max_length=400)
    options: list[str] = Field(min_length=4, max_length=4)
    answer_index: int = Field(ge=0, le=3)
    explanation: str = ""


# --------------------------------------------------------------------------- #
# Shared helpers (also used by the path and dashboard routers)
# --------------------------------------------------------------------------- #
def load_learner(db: Session, learner_id: int) -> Learner:
    learner = db.get(Learner, learner_id)
    if learner is None:
        raise HTTPException(status_code=404, detail=f"no learner with id {learner_id}")
    return learner


def load_mastery(db: Session, learner_id: int) -> MasteryTable:
    """Read every mastery row for one learner into the in-memory model."""
    rows = db.exec(select(Mastery).where(Mastery.learner_id == learner_id)).all()
    return MasteryTable(
        {
            row.skill_id: MasteryValue(
                score=row.score, source=row.source, confidence=row.confidence
            )
            for row in rows
        }
    )


def persist_mastery(db: Session, learner_id: int, table: MasteryTable) -> None:
    """Write back only the rows the update rules actually changed."""
    if not table.dirty:
        return
    existing = {
        row.skill_id: row
        for row in db.exec(select(Mastery).where(Mastery.learner_id == learner_id)).all()
    }
    for skill_id in sorted(table.dirty):
        value = table.get(skill_id)
        row = existing.get(skill_id) or Mastery(learner_id=learner_id, skill_id=skill_id)
        row.score, row.source, row.confidence = value.score, value.source, value.confidence
        db.add(row)
    table.dirty.clear()
    db.commit()


def gap_for(graph: SkillGraph, learner: Learner, table: MasteryTable) -> set[str]:
    goals = [g for g in learner.goal_node_ids if g in graph]
    return table.gap(graph.required_for(goals)) if goals else set()


# --------------------------------------------------------------------------- #
# Item selection
# --------------------------------------------------------------------------- #
def _select_skill(
    graph: SkillGraph, gap: set[str], table: MasteryTable, already_asked: set[str]
) -> SkillNode | None:
    """Highest `uncertainty x (1 + downstream unlocks)`, ties broken by id."""
    candidates = [
        graph.require(skill_id)
        for skill_id in gap
        if graph.require(skill_id).assessable and skill_id not in already_asked
    ]
    if not candidates:
        return None

    def utility(node: SkillNode) -> tuple[float, str]:
        uncertainty = 1.0 - table.get(node.id).confidence
        return (-uncertainty * (1 + graph.downstream_unlock_count(node.id)), node.id)

    return min(candidates, key=utility)


def _generate_question(node: SkillNode) -> tuple[GeneratedQuestion, bool]:
    """Ask the model for one item; fall back to the deterministic mock."""
    prompt = QUIZ_GENERATION.format(
        skill_name=node.name,
        skill_description=node.description,
        keywords=", ".join(node.keywords),
        difficulty=node.difficulty,
    )
    provider = get_provider()
    if provider.available():
        try:
            return call_with_schema(provider, prompt, GeneratedQuestion, temperature=0.6), False
        except (SchemaViolation, ProviderUnavailable) as exc:
            logger.info("quiz generation degraded for %s: %s", node.id, str(exc)[:120])

    return GeneratedQuestion(**json.loads(MockProvider().complete(prompt))), True


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/next/{learner_id}", response_model=DiagnosticQuestion)
def next_question(
    learner_id: int, db: Session = Depends(get_session)
) -> DiagnosticQuestion:
    """The next most informative question, or `done` when measurement suffices."""
    settings = get_settings()
    learner = load_learner(db, learner_id)
    graph = load_graph()
    table = load_mastery(db, learner_id)

    asked_items = db.exec(
        select(QuizItem).where(QuizItem.learner_id == learner_id, QuizItem.kind == "diagnostic")
    ).all()
    answered = [q for q in asked_items if q.chosen_index is not None or q.dont_know]
    gap = gap_for(graph, learner, table)
    current_confidence = confidence(table, gap, graph)

    pending = next((q for q in asked_items if q.chosen_index is None and not q.dont_know), None)
    if pending is not None:
        return _as_question(pending, graph, len(answered), settings.diagnostic_max_questions,
                            current_confidence, degraded=False)

    if (
        len(answered) >= settings.diagnostic_max_questions
        or current_confidence >= settings.diagnostic_confidence_target
    ):
        return DiagnosticQuestion(
            done=True, asked=len(answered),
            max_questions=settings.diagnostic_max_questions, confidence=current_confidence,
        )

    node = _select_skill(graph, gap, table, {q.skill_id for q in asked_items})
    if node is None:
        return DiagnosticQuestion(
            done=True, asked=len(answered),
            max_questions=settings.diagnostic_max_questions, confidence=current_confidence,
        )

    generated, degraded = _generate_question(node)
    item = QuizItem(
        learner_id=learner_id,
        skill_id=node.id,
        kind="diagnostic",
        question=generated.question,
        options=generated.options,
        answer_index=generated.answer_index,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    return _as_question(item, graph, len(answered), settings.diagnostic_max_questions,
                        current_confidence, degraded)


def _as_question(
    item: QuizItem, graph: SkillGraph, asked: int, maximum: int,
    current_confidence: float, degraded: bool,
) -> DiagnosticQuestion:
    """Project a stored item onto the wire shape -- without the answer key."""
    return DiagnosticQuestion(
        done=False,
        quiz_item_id=item.id,
        skill_id=item.skill_id,
        skill_name=graph.require(item.skill_id).name if item.skill_id in graph else item.skill_id,
        question=item.question,
        options=item.options,
        asked=asked,
        max_questions=maximum,
        confidence=current_confidence,
        llm_degraded=degraded,
    )


@router.post("/answer", response_model=DiagnosticAnswerResponse)
def answer(
    payload: DiagnosticAnswerRequest, db: Session = Depends(get_session)
) -> DiagnosticAnswerResponse:
    """Grade deterministically against the stored key and update mastery."""
    item = db.get(QuizItem, payload.quiz_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="unknown quiz item")
    if item.chosen_index is not None or item.dont_know:
        raise HTTPException(status_code=409, detail="that question was already answered")

    settings = get_settings()
    graph = load_graph()
    learner = load_learner(db, item.learner_id)
    table = load_mastery(db, item.learner_id)

    is_correct = (not payload.dont_know) and payload.chosen_index == item.answer_index
    item.chosen_index = None if payload.dont_know else payload.chosen_index
    item.dont_know = payload.dont_know
    item.correct = is_correct
    db.add(item)

    apply_answer(
        table, graph,
        skill_id=item.skill_id,
        correct=is_correct,
        dont_know=payload.dont_know,
        question_id=item.id,
        source="milestone" if item.kind == "milestone" else "diagnostic",
    )
    persist_mastery(db, item.learner_id, table)

    answered = db.exec(
        select(QuizItem).where(QuizItem.learner_id == item.learner_id, QuizItem.kind == "diagnostic")
    ).all()
    answered_count = sum(1 for q in answered if q.chosen_index is not None or q.dont_know)

    gap = gap_for(graph, learner, table)
    new_confidence = confidence(table, gap, graph)
    done = (
        answered_count >= settings.diagnostic_max_questions
        or new_confidence >= settings.diagnostic_confidence_target
        or _select_skill(graph, gap, table, {q.skill_id for q in answered}) is None
    )

    db.add(Event(
        learner_id=item.learner_id, type="diagnostic_answered",
        payload={"skill_id": item.skill_id, "correct": is_correct,
                 "dont_know": payload.dont_know, "confidence": new_confidence},
    ))
    db.commit()

    return DiagnosticAnswerResponse(
        correct=is_correct,
        skill_id=item.skill_id,
        new_score=table.score(item.skill_id),
        confidence=new_confidence,
        asked=answered_count,
        done=done,
    )
