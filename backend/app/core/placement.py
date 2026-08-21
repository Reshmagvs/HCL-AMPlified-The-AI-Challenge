"""Placement questions for skills the curated bank never covered.

The shipped bank holds 144 questions, one per curated skill, written and
position-balanced offline. A discovered topic has none, and the diagnostic
cannot measure what it cannot ask about.

Generating them inside the diagnostic was the obvious approach and the wrong
one: one question costs about twenty seconds on a local model, so a learner
answering eight of them would spend nearly three minutes watching a spinner
between questions. Worse, nothing was written down, so the next learner paid
again.

So they are written **once, in the background, in a single batched call**, as
soon as a topic finishes building. One call for nine skills costs roughly what
two individual ones do, and it happens while the learner is still reading the
"your subject is ready" screen and filling in their hours. By the time they
start the placement check the questions are usually there; if they are not, the
diagnostic simply asks about the skills it *can* measure, which is a state it
already handles.

Everything written here goes through the same overlay as discovered skills and
resources, so it survives a restart and is shared by every learner afterwards.
"""

from __future__ import annotations

import logging
import threading

from pydantic import BaseModel, Field

from app.core import questions, store
from app.core.skill_graph import SkillNode, load_graph
from app.llm import get_provider
from app.llm.base import ProviderUnavailable, SchemaViolation, call_with_schema
from app.llm.prompts import QUIZ_BATCH

logger = logging.getLogger(__name__)

# One call covers this many skills. Beyond it the reply grows long enough that a
# small model starts dropping fields, and a failed batch wastes everything in it.
BATCH_SIZE = 6


class BankedItem(BaseModel):
    question: str = Field(min_length=8, max_length=400)
    options: list[str] = Field(min_length=4, max_length=4)
    answer_index: int = Field(ge=0, le=3)
    explanation: str = ""


class QuestionBatch(BaseModel):
    by_skill: dict[str, BankedItem] = Field(default_factory=dict)


def _schema(skill_ids: list[str]) -> dict:
    """Force a complete item for every skill, rather than a well-formed shrug."""
    item = {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 4,
                "maxItems": 4,
            },
            "answer_index": {"type": "integer"},
            "explanation": {"type": "string"},
        },
        "required": ["question", "options", "answer_index", "explanation"],
    }
    return {
        "type": "object",
        "properties": {
            "by_skill": {
                "type": "object",
                "properties": {skill_id: item for skill_id in skill_ids},
                "required": skill_ids,
            }
        },
        "required": ["by_skill"],
    }


def _describe(node: SkillNode) -> str:
    return (
        f"- id: {node.id}\n"
        f"  name: {node.name}\n"
        f"  description: {node.description}\n"
        f"  keywords: {', '.join(node.keywords)}\n"
        f"  difficulty: {node.difficulty}"
    )


def write_for(skill_ids: list[str]) -> int:
    """Generate and store questions for these skills. Returns how many were written."""
    graph = load_graph()
    existing = questions.load_questions()
    wanted = [s for s in skill_ids if s in graph and s not in existing]
    if not wanted:
        return 0

    provider = get_provider()
    if provider.name == "mock" or not provider.available():
        logger.info("no model available to write placement questions")
        return 0

    written = 0
    for start in range(0, len(wanted), BATCH_SIZE):
        batch = wanted[start : start + BATCH_SIZE]
        prompt = QUIZ_BATCH.format(
            skill_block="\n".join(_describe(graph.require(s)) for s in batch)
        )
        try:
            result = call_with_schema(
                provider,
                prompt,
                QuestionBatch,
                temperature=0.5,
                max_tokens=2400,
                json_schema=_schema(batch),
            )
        except (SchemaViolation, ProviderUnavailable) as exc:
            logger.warning("placement batch failed for %s: %s", batch, str(exc)[:140])
            continue

        usable = {
            skill_id: {
                "question": item.question,
                "options": item.options,
                "answer_index": item.answer_index,
                "explanation": item.explanation,
                "generated": True,
            }
            for skill_id, item in result.by_skill.items()
            if skill_id in batch and len(set(item.options)) == 4
        }
        if usable:
            store.append_questions(usable)
            questions.reset_cache()
            written += len(usable)

    logger.info("wrote %d placement questions", written)
    return written


def write_in_background(skill_ids: list[str]) -> None:
    """Start writing questions without making anybody wait for them."""
    if not skill_ids:
        return
    threading.Thread(
        target=_safely, args=(list(skill_ids),), name="placement-questions", daemon=True
    ).start()


def _safely(skill_ids: list[str]) -> None:
    try:
        write_for(skill_ids)
    except Exception:  # noqa: BLE001 -- a background thread must not die silently
        logger.exception("placement question generation failed")
