"""The diagnostic question bank, loaded from disk.

Quiz items do not vary by learner -- only which ones get asked does. Generating
them per request made the most important screen in the product depend on an API
being reachable, cost three seconds of waiting per question, and produced
slightly different wording for every learner, which made the bank impossible to
review.

So the questions are generated once offline (``scripts/build_questions.py``),
audited, balanced so the correct answer is evenly spread across the four
positions, and committed as ``data/questions.json``. This module is the read
side: a dict lookup, microseconds, no network, identical for everyone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BankedQuestion:
    """One item as stored. The answer never leaves the server."""

    skill_id: str
    question: str
    options: tuple[str, ...]
    answer_index: int
    explanation: str = ""


@lru_cache(maxsize=1)
def load_questions() -> dict[str, BankedQuestion]:
    """Read data/questions.json once per process."""
    target = get_settings().data_dir / "questions.json"
    if not target.exists():
        logger.warning(
            "questions.json not found at %s -- the diagnostic will fall back to "
            "generating items at request time",
            target,
        )
        return {}

    raw = json.loads(target.read_text(encoding="utf-8"))
    bank = {
        skill_id: BankedQuestion(
            skill_id=skill_id,
            question=item["question"],
            options=tuple(item["options"]),
            answer_index=int(item["answer_index"]),
            explanation=item.get("explanation", ""),
        )
        for skill_id, item in raw.items()
        if len(item.get("options", [])) == 4
    }
    logger.info("question bank loaded: %d items", len(bank))
    return bank


def get_question(skill_id: str) -> BankedQuestion | None:
    return load_questions().get(skill_id)


def reset_cache() -> None:
    load_questions.cache_clear()
