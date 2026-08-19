"""Grounded Q&A over one learner's own plan.

The context handed to the model is assembled here from database rows and nothing
else: this learner's goal, their weeks, the resources actually bound to their
path, their measured mastery, and -- for each step -- the dependency chain that
made it necessary. That last part matters: without it the assistant could only
say *what* is in the path, and "why is linear algebra in my path?" is the
question learners actually ask. The model is instructed to answer only from
that block and to say so plainly when the answer is not in it.

Two consequences worth stating explicitly. A question about a course that is not
in this learner's path cannot be answered with an invented course, because no
other course appears in the context. And with the provider unavailable the
endpoint still answers -- from a deterministic summary of the same rows, marked
`llm_degraded` -- rather than returning an error.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.core.retrieval import catalog_index
from app.core.skill_graph import load_graph
from app.db import get_session
from app.llm import get_provider
from app.llm.base import ProviderUnavailable, SchemaViolation, call_with_schema
from app.llm.prompts import CHAT_GROUNDED
from app.models import Event, PathItem
from app.pathing import latest_path, load_items
from app.routers.diagnostic import load_learner, load_mastery
from app.schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_QUESTION_CHARS = 500
CONTEXT_ITEM_LIMIT = 24


class ChatReply(BaseModel):
    reply: str = Field(min_length=1, max_length=1200)


def _context_block(learner, items: list[PathItem], version: int) -> tuple[str, list[dict[str, str]]]:
    """Everything the model is allowed to know, plus the citations it maps to."""
    graph = load_graph()
    catalog = catalog_index()
    goals = ", ".join(graph.require(g).name for g in learner.goal_node_ids if g in graph)

    lines = [
        f"Learner goal: {goals or learner.goal_text}",
        f"Capacity: {learner.hours_per_week:g} hours per week",
        f"Preferences: format={learner.format_pref}, cost={learner.cost_pref}, "
        f"language={learner.language}, low_bandwidth={learner.low_bandwidth}",
        f"Path version {version}. Steps:",
    ]
    citations: list[dict[str, str]] = []

    for item in items[:CONTEXT_ITEM_LIMIT]:
        name = graph.require(item.skill_id).name if item.skill_id in graph else item.skill_id
        resource = catalog.get(item.course_id) if item.course_id else None
        title = resource.title if resource else "no resource bound"
        chain = (item.provenance.get("why_needed") or {}).get("path_to_goal") or []
        because = f"; needed because it leads to {' -> '.join(chain[:3])}" if chain else ""
        level = (item.provenance.get("your_level") or {}).get("score")
        measured = f"; your level {level:.0%}" if isinstance(level, (int, float)) else ""
        lines.append(
            f"  week {item.week_number} [{item.status}] {name} ({item.kind}) "
            f"-- {title}, {item.est_hours:g}h{because}{measured}"
        )
        if resource:
            citations.append({"title": resource.title, "url": resource.url, "skill": name})
    if len(items) > CONTEXT_ITEM_LIMIT:
        lines.append(f"  ... and {len(items) - CONTEXT_ITEM_LIMIT} further steps")
    return "\n".join(lines), citations


def _deterministic_reply(learner, items: list[PathItem]) -> str:
    """A factual summary used when no model is available."""
    graph = load_graph()
    upcoming = [i for i in items if i.status != "done"][:3]
    if not upcoming:
        return "Every step in your path is complete. Generate a new path to keep going."
    listed = "; ".join(
        f"week {i.week_number}: {graph.require(i.skill_id).name if i.skill_id in graph else i.skill_id}"
        for i in upcoming
    )
    return (
        f"Running without a language model, so here is your plan data directly. "
        f"Your next steps are {listed}. You have {learner.hours_per_week:g} hours a week "
        f"budgeted, and the full path finishes in week "
        f"{max((i.week_number for i in items), default=0)}."
    )


@router.post("/{learner_id}", response_model=ChatResponse)
def chat(
    learner_id: int, payload: ChatRequest, db: Session = Depends(get_session)
) -> ChatResponse:
    """Answer a question using only this learner's stored plan."""
    question = payload.message.strip()[:MAX_QUESTION_CHARS]
    if not question:
        raise HTTPException(status_code=422, detail="message must not be empty")

    learner = load_learner(db, learner_id)
    path = latest_path(db, learner_id)
    items = load_items(db, path.id) if path else []
    load_mastery(db, learner_id)  # touched so a missing learner fails the same way everywhere

    if not items:
        return ChatResponse(
            reply="You do not have a path yet. Finish intake and generate one, then ask me again.",
            citations=[],
            llm_degraded=False,
        )

    context, citations = _context_block(learner, items, path.version if path else 0)
    provider = get_provider()
    degraded = True
    reply = _deterministic_reply(learner, items)

    if provider.available():
        prompt = CHAT_GROUNDED.format(context=context, question=question)
        try:
            reply = call_with_schema(provider, prompt, ChatReply, temperature=0.3, max_tokens=600).reply
            degraded = False
        except (SchemaViolation, ProviderUnavailable) as exc:
            logger.info("chat degraded for learner %s: %s", learner_id, str(exc)[:120])

    db.add(Event(learner_id=learner_id, type="chat_question",
                 payload={"question": question, "llm_degraded": degraded}))
    db.commit()
    return ChatResponse(reply=reply.strip(), citations=citations[:6], llm_degraded=degraded)
