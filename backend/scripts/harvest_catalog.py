"""Propose candidate learning resources for the skill graph.

Manual curation of 400+ resources is not available here, so the catalog is built
by a **propose-then-verify** loop. This script is only the propose half, and it
is written on the assumption that some of what it collects is wrong: the prompt
tells the model plainly that every URL will be fetched over HTTP and discarded
if it does not return 2xx, and that three resources it is confident about beat
eight it is not.

Nothing here is trusted. Output goes to ``data/courses_raw.json``, which is
gitignored and never read at runtime -- only ``verify_catalog.py`` promotes an
entry into ``data/courses.json``.

Two operational details matter more than they look:

**Skills are batched.** The free Gemini tier allows a handful of requests per
minute, and the graph has 152 nodes. Asking about six related skills per call
turns a 30-minute serial grind into a few minutes, and grouping by track means
the model sees coherent context rather than six unrelated topics.

**Rate limits are obeyed, not fought.** A 429 carries a ``retryDelay``; the
script parses it and sleeps exactly that long instead of hammering with a fixed
backoff.

Resumable: a skill already present in the raw file is skipped unless ``--force``
is passed, so an interrupted run picks up where it stopped.

    python -m scripts.harvest_catalog --track machine-learning
    python -m scripts.harvest_catalog --all
    python -m scripts.harvest_catalog --skills ml.cnn,ml.transformers --force
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.skill_graph import SkillNode, load_graph  # noqa: E402
from app.llm import get_provider  # noqa: E402
from app.llm.base import ProviderUnavailable, SchemaViolation, call_with_schema  # noqa: E402
from app.llm import prompts  # noqa: E402
from app.llm.prompts import HARVEST_CANDIDATES_BATCH  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

logger = logging.getLogger("harvest")

RAW_PATH = get_settings().data_dir / "courses_raw.json"
_RETRY_DELAY_RE = re.compile(r"retry(?:_|\s*)?[dD]elay['\":\s]+(\d+(?:\.\d+)?)s?")


class Candidate(BaseModel):
    """One proposed resource, before any of it has been checked."""

    title: str = Field(min_length=3, max_length=200)
    provider: str = Field(min_length=2, max_length=80)
    url: HttpUrl
    format: Literal["video", "text", "interactive", "course"]
    cost: Literal["free", "paid"]
    duration_hours: float = Field(gt=0, le=200)
    level: Literal["beginner", "intermediate", "advanced"]
    rating: float = Field(ge=3.0, le=5.0)
    description: str = ""


class HarvestBatch(BaseModel):
    by_skill: dict[str, list[Candidate]] = Field(default_factory=dict)


def load_raw() -> dict[str, list[dict[str, Any]]]:
    """Read what previous runs collected, keyed by skill id."""
    if not RAW_PATH.exists():
        return {}
    return json.loads(RAW_PATH.read_text(encoding="utf-8"))


def save_raw(raw: dict[str, list[dict[str, Any]]]) -> None:
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(raw, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _retry_delay(message: str, default: float) -> float:
    """Honour the server's own retry hint when it gives one."""
    match = _RETRY_DELAY_RE.search(message)
    return min(float(match.group(1)) + 1.0, 90.0) if match else default


def _skill_block(nodes: list[SkillNode]) -> str:
    return "\n".join(
        f"- id: {n.id}\n  name: {n.name}\n  about: {n.description}\n"
        f"  topics: {', '.join(n.keywords)}\n  difficulty: {n.difficulty}/5"
        for n in nodes
    )


EMPHASIS = {
    "default": prompts.EMPHASIS_DEFAULT,
    "video": prompts.EMPHASIS_VIDEO,
    "india": prompts.EMPHASIS_INDIA,
    "docs": prompts.EMPHASIS_DOCS,
}


def harvest_batch(
    nodes: list[SkillNode], emphasis: str = "default", attempts: int = 4
) -> dict[str, list[dict[str, Any]]]:
    """Ask for candidates covering a group of skills, retrying through rate limits."""
    prompt = HARVEST_CANDIDATES_BATCH.format(
        skill_block=_skill_block(nodes), emphasis=EMPHASIS[emphasis]
    )
    wanted = {n.id for n in nodes}
    provider = get_provider()

    for attempt in range(1, attempts + 1):
        try:
            batch = call_with_schema(provider, prompt, HarvestBatch, temperature=0.3, max_tokens=8192)
            return {
                skill_id: [c.model_dump(mode="json") for c in candidates]
                for skill_id, candidates in batch.by_skill.items()
                if skill_id in wanted  # a skill id we did not ask about is discarded
            }
        except SchemaViolation as exc:
            logger.warning("batch schema violation, skipping: %s", str(exc)[:140])
            return {}
        except ProviderUnavailable as exc:
            wait = _retry_delay(str(exc), default=8.0 * attempt)
            logger.warning("provider unavailable, sleeping %.0fs (attempt %d)", wait, attempt)
            time.sleep(wait)
    logger.error("batch %s gave up after %d attempts", [n.id for n in nodes], attempts)
    return {}


def merge_for_skill(
    existing: list[dict[str, Any]], fresh: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Union two candidate lists for one skill, keyed by URL.

    Several harvest passes accumulate coverage rather than overwriting it -- one
    pass alone tends to return the same three obvious resources per skill, and
    the catalog needs breadth for the alternatives ranking to mean anything.
    """
    seen = {c["url"].rstrip("/").lower() for c in existing}
    merged = list(existing)
    for candidate in fresh:
        key = candidate["url"].rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            merged.append(candidate)
    return merged


def select_nodes(args: argparse.Namespace) -> list[SkillNode]:
    """Resolve --track / --all / --skills into the nodes to harvest."""
    graph = load_graph()
    if args.skills:
        return [graph.require(s.strip()) for s in args.skills.split(",") if s.strip()]
    if args.all:
        # Grouped by track so each batch shares context.
        return [n for track in graph.tracks for n in graph.by_track(track)]
    if not args.track:
        raise SystemExit("pass --track NAME, --all, or --skills id1,id2")
    nodes = graph.by_track(args.track)
    if not nodes:
        raise SystemExit(f"unknown track {args.track!r}; known: {', '.join(graph.tracks)}")
    return nodes


def main() -> int:
    parser = argparse.ArgumentParser(description="Propose candidate learning resources.")
    parser.add_argument("--track", help="harvest every node in one track")
    parser.add_argument("--all", action="store_true", help="harvest every node in the graph")
    parser.add_argument("--skills", help="comma-separated skill ids")
    parser.add_argument("--force", action="store_true", help="re-harvest skills already collected")
    parser.add_argument(
        "--append", action="store_true",
        help="keep existing candidates and merge new ones in (implies --force)",
    )
    parser.add_argument("--batch", type=int, default=6, help="skills per model call")
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between calls")
    parser.add_argument(
        "--emphasis", choices=sorted(EMPHASIS), default="default",
        help="steer a pass towards video, Indian platforms or official docs",
    )
    args = parser.parse_args()

    configure_logging(get_settings().log_level)
    if get_provider().name == "mock":
        logger.error(
            "the mock provider never proposes resources -- set LLM_PROVIDER=gemini "
            "and GEMINI_API_KEY in .env before harvesting"
        )
        return 2

    raw = load_raw()
    args.force = args.force or args.append
    pending = [n for n in select_nodes(args) if args.force or not raw.get(n.id)]
    if not pending:
        logger.info("nothing to harvest -- every requested skill already has candidates")
        return 0

    batches = [pending[i : i + args.batch] for i in range(0, len(pending), args.batch)]
    logger.info("harvesting %d skills in %d batches", len(pending), len(batches))

    for index, group in enumerate(batches, start=1):
        found = harvest_batch(group, args.emphasis)
        for node in group:
            fresh = found.get(node.id, [])
            raw[node.id] = merge_for_skill(raw.get(node.id, []), fresh) if args.append else (
                fresh or raw.get(node.id, [])
            )
        save_raw(raw)  # checkpoint after every batch so a crash loses one batch at most
        logger.info(
            "batch %d/%d  %-28s +%d candidates",
            index, len(batches), group[0].id, sum(len(v) for v in found.values()),
        )
        if index < len(batches):
            time.sleep(args.pause)

    total = sum(len(v) for v in raw.values())
    empty = sorted(k for k, v in raw.items() if not v)
    logger.info("raw catalog holds %d candidates across %d skills", total, len(raw))
    if empty:
        logger.warning("%d skills still have zero candidates: %s", len(empty), empty[:10])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
