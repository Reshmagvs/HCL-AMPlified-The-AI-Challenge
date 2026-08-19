"""Build the diagnostic question bank shipped with the product.

Generating quiz items at request time made the most important screen in the
product depend on an API being up: a rate limit turned "measure what you know"
into a template, and every learner waited three seconds per question for text
that is identical for everyone anyway.

The questions do not vary by learner -- only *which* questions get asked does.
So they are generated once here, verified structurally, and committed as
``data/questions.json``. The diagnostic then reads from disk in microseconds,
works offline, and is identical for every learner, which also makes it far
easier to review and correct by hand.

Runtime generation still exists as a fallback for a skill with no banked
question, so adding a node to the graph does not break the diagnostic.

    python -m scripts.build_questions                    # only missing skills
    python -m scripts.build_questions --force            # rebuild everything
    python -m scripts.build_questions --skills ml.cnn    # one skill
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.skill_graph import SkillNode, load_graph  # noqa: E402
from app.llm import get_provider  # noqa: E402
from app.llm.base import ProviderUnavailable, SchemaViolation, call_with_schema  # noqa: E402
from app.llm.prompts import QUIZ_BATCH  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

logger = logging.getLogger("questions")

BANK_PATH = get_settings().data_dir / "questions.json"
_RETRY_DELAY_RE = re.compile(r"retry(?:_|\s*)?[dD]elay['\":\s]+(\d+(?:\.\d+)?)s?")


class Question(BaseModel):
    """One banked item. Validated hard, because a broken one ships forever."""

    question: str = Field(min_length=12, max_length=400)
    options: list[str] = Field(min_length=4, max_length=4)
    answer_index: int = Field(ge=0, le=3)
    explanation: str = ""

    @field_validator("options")
    @classmethod
    def _distinct_and_substantial(cls, options: list[str]) -> list[str]:
        cleaned = [o.strip() for o in options]
        if any(len(o) < 2 for o in cleaned):
            raise ValueError("every option needs real text")
        if len({o.lower() for o in cleaned}) != 4:
            raise ValueError("options must be distinct")
        return cleaned


class QuestionBatch(BaseModel):
    by_skill: dict[str, Question] = Field(default_factory=dict)


def load_bank() -> dict[str, dict[str, Any]]:
    if not BANK_PATH.exists():
        return {}
    return json.loads(BANK_PATH.read_text(encoding="utf-8"))


def save_bank(bank: dict[str, dict[str, Any]]) -> None:
    ordered = {k: bank[k] for k in sorted(bank)}
    BANK_PATH.write_text(json.dumps(ordered, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _retry_delay(message: str, default: float) -> float:
    match = _RETRY_DELAY_RE.search(message)
    return min(float(match.group(1)) + 1.0, 90.0) if match else default


def _skill_block(nodes: list[SkillNode]) -> str:
    return "\n".join(
        f"- id: {n.id}\n  name: {n.name}\n  about: {n.description}\n"
        f"  topics: {', '.join(n.keywords)}\n  difficulty: {n.difficulty}/5"
        for n in nodes
    )


def generate_batch(nodes: list[SkillNode], attempts: int = 4) -> dict[str, dict[str, Any]]:
    """Ask for one question per skill, retrying through rate limits."""
    prompt = QUIZ_BATCH.format(skill_block=_skill_block(nodes))
    wanted = {n.id for n in nodes}
    provider = get_provider()

    for attempt in range(1, attempts + 1):
        try:
            batch = call_with_schema(provider, prompt, QuestionBatch, temperature=0.7, max_tokens=8192)
            return {
                skill_id: question.model_dump()
                for skill_id, question in batch.by_skill.items()
                if skill_id in wanted
            }
        except SchemaViolation as exc:
            logger.warning("batch rejected: %s", str(exc)[:140])
            return {}
        except ProviderUnavailable as exc:
            wait = _retry_delay(str(exc), default=8.0 * attempt)
            logger.warning("provider unavailable, sleeping %.0fs (attempt %d)", wait, attempt)
            time.sleep(wait)
    logger.error("gave up on %s", [n.id for n in nodes])
    return {}


def balance_positions(bank: dict[str, dict[str, Any]]) -> int:
    """Spread correct answers evenly across the four positions.

    Left alone, the model put 49% of correct answers at position B and one at
    position D -- a bank a learner could beat by always picking the second
    option, which would make the whole diagnostic meaningless. Rotating each
    question's options so the correct one lands at ``index % 4`` fixes the
    distribution exactly, is deterministic, and changes no wording.
    """
    changed = 0
    for position, skill_id in enumerate(sorted(bank)):
        item = bank[skill_id]
        options = list(item["options"])
        target = position % len(options)
        current = item["answer_index"]
        if current == target:
            continue
        shift = (current - target) % len(options)
        item["options"] = options[shift:] + options[:shift]
        item["answer_index"] = target
        changed += 1
    return changed


def audit(bank: dict[str, dict[str, Any]], graph) -> list[str]:
    """Structural problems a human should look at before shipping the bank."""
    problems: list[str] = []
    for skill_id, item in bank.items():
        if skill_id not in graph:
            problems.append(f"{skill_id}: not a skill in the graph")
            continue
        try:
            Question.model_validate(item)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{skill_id}: {str(exc)[:90]}")

    positions = [item.get("answer_index") for item in bank.values()]
    for index in range(4):
        share = positions.count(index) / max(1, len(positions))
        if share > 0.45:
            problems.append(f"answer position {index} holds {share:.0%} of the bank")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the diagnostic question bank.")
    parser.add_argument("--skills", help="comma-separated skill ids")
    parser.add_argument("--force", action="store_true", help="regenerate skills already banked")
    parser.add_argument("--batch", type=int, default=6)
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()

    configure_logging(get_settings().log_level)
    graph = load_graph()
    bank = load_bank()

    if args.audit_only:
        problems = audit(bank, graph)
        print(f"\n  bank holds {len(bank)} questions")
        for problem in problems:
            print(f"  !! {problem}")
        print("  no structural problems\n" if not problems else "")
        return 1 if problems else 0

    if get_provider().name == "mock":
        logger.error("set LLM_PROVIDER to gemini or ollama before building the bank")
        return 2

    if args.skills:
        wanted = [graph.require(s.strip()) for s in args.skills.split(",") if s.strip()]
    else:
        wanted = [n for n in graph.nodes.values() if n.assessable]
    pending = [n for n in wanted if args.force or n.id not in bank]

    if not pending:
        logger.info("every requested skill already has a banked question")
        return 0

    batches = [pending[i : i + args.batch] for i in range(0, len(pending), args.batch)]
    logger.info("generating %d questions in %d batches", len(pending), len(batches))

    for index, group in enumerate(batches, start=1):
        produced = generate_batch(group)
        bank.update(produced)
        save_bank(bank)  # checkpoint, so a crash costs one batch
        logger.info("batch %d/%d  %-28s +%d", index, len(batches), group[0].id, len(produced))
        if index < len(batches):
            time.sleep(args.pause)

    rotated = balance_positions(bank)
    save_bank(bank)
    logger.info("balanced answer positions (%d questions rotated)", rotated)

    missing = sorted(n.id for n in wanted if n.id not in bank)
    problems = audit(bank, graph)
    logger.info("bank holds %d questions; %d assessable skills still missing",
                len(bank), len(missing))
    if missing:
        logger.warning("missing: %s", missing[:10])
    for problem in problems:
        logger.warning("audit: %s", problem)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
