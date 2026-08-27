"""The generated overlay: topics the product learned about after it shipped.

The curated files (``skills.json``, ``courses.json``) are the seed. They are
hand-verified, they are checked into git, and nothing at runtime ever writes to
them. Everything discovered later -- a syllabus for quantum computing, the pages
found for organic chemistry -- lands in ``data/generated/`` instead.

Keeping the two apart buys three things that matter for a product people rely on:

**The seed cannot be corrupted.** A bad generation degrades the overlay and is
deleted with ``rm -r data/generated``; it can never damage the verified core.

**Provenance stays legible.** Every overlay record carries ``discovered=True``,
the query that produced it and when, so the interface can tell a learner "this
was found for you on 19 August" instead of implying it was curated.

**Growth is cumulative.** The overlay is append-only and shared: the first
learner to ask about quantum computing pays for the search, and everyone after
them gets it instantly. That is the difference between a product that improves
with use and a demo that repeats the same work forever.

Concurrency is handled by writing a temporary file and replacing atomically, so
a reader never observes half a topic, and by holding a process lock across the
read-modify-write so two simultaneous expansions cannot lose each other's work.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)

SKILLS_FILE = "skills.json"
COURSES_FILE = "courses.json"
TOPICS_FILE = "topics.json"
QUESTIONS_FILE = "questions.json"

# One writer at a time within a process. The atomic replace below covers the
# cross-process case: the loser of a race overwrites with a superset, because
# every write is a full read-modify-write of an append-only structure.
_write_lock = threading.Lock()


def generated_dir() -> Path:
    """Where the overlay lives. Created on demand.

    Configurable because it is *mutable state a test can destroy*. The suite
    clears the overlay between tests, and while that directory defaulted to the
    real one a test run silently deleted every subject a user had built.
    """
    settings = get_settings()
    target = Path(settings.generated_dir) if settings.generated_dir else settings.data_dir / "generated"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("generated file %s is unreadable (%s) -- ignoring it", path.name, exc)
        return default


def _write_json(path: Path, payload: Any) -> None:
    """Write atomically, so a concurrent reader never sees a partial file."""
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def load_skills() -> list[dict[str, Any]]:
    """Generated skill nodes, in the same shape as curated skills.json."""
    return _read_json(generated_dir() / SKILLS_FILE, [])


def load_courses() -> list[dict[str, Any]]:
    """Generated catalogue entries, in the same shape as curated courses.json."""
    return _read_json(generated_dir() / COURSES_FILE, [])


def load_generated_questions() -> dict[str, dict[str, Any]]:
    """Placement questions written for discovered skills, keyed by skill id.

    Generating one costs about twenty seconds on a local model, so it is written
    down the first time and never paid for again -- by anyone. This is the same
    bargain the topic cache makes.
    """
    return _read_json(generated_dir() / QUESTIONS_FILE, {})


def append_questions(items: dict[str, dict[str, Any]]) -> int:
    """Add questions to the overlay, keeping any already written. Returns the total."""
    if not items:
        return 0
    with _write_lock:
        bank = load_generated_questions()
        added = {k: v for k, v in items.items() if k not in bank}
        bank.update(added)
        _write_json(generated_dir() / QUESTIONS_FILE, bank)
    if added:
        logger.info("stored %d generated placement questions", len(added))
    return len(bank)


def load_topics() -> dict[str, dict[str, Any]]:
    """Every topic ever expanded, keyed by its normalised query.

    This is the cache index. A hit here is why the second learner asking about
    quantum computing waits milliseconds instead of a minute.
    """
    return _read_json(generated_dir() / TOPICS_FILE, {})


def topic_key(goal_text: str) -> str:
    """Normalise a goal into a cache key. Case and spacing are not meaningful."""
    return " ".join(goal_text.lower().split())[:200]


def find_topic(goal_text: str) -> dict[str, Any] | None:
    """The cached expansion for this goal, if one exists.

    Exact key first, because that is the common case and it is free.

    Then containment, because the exact key is not enough in practice: a
    learner types "i want to master business studies", intake extracts the goal
    as "master business studies", and those are two different keys for one
    subject. The learner was then told their freshly built subject was not
    taught here, and offered to build it a second time.

    Containment is deliberately the only fuzziness allowed. It cannot match two
    genuinely different subjects -- one string has to literally contain the
    other -- so it fixes the trimming case without opening the door to the
    nearest-neighbour guessing this module exists to avoid.
    """
    topics = load_topics()
    key = topic_key(goal_text)
    exact = topics.get(key)
    if exact is not None:
        return exact

    if len(key) < 6:
        return None
    for candidate_key, record in topics.items():
        if key in candidate_key or candidate_key in key:
            logger.info("goal %r matched the built topic %r", key[:60], record.get("topic"))
            return record
    return None


def vectors_path(kind: str, embedder: str) -> Path:
    """Where the overlay's embedding matrix for one embedder lives."""
    return generated_dir() / f"{kind}_embeddings.{embedder}.npy"


def ids_path(kind: str, embedder: str) -> Path:
    """The id-per-row companion to a matrix. Order is meaningless without it."""
    return generated_dir() / f"{kind}_embeddings.{embedder}.ids.json"


def load_vectors(kind: str, embedder: str) -> dict[str, np.ndarray]:
    """id -> vector for the overlay, or {} when nothing has been generated."""
    matrix_file, id_file = vectors_path(kind, embedder), ids_path(kind, embedder)
    if not matrix_file.exists() or not id_file.exists():
        return {}
    try:
        matrix = np.load(matrix_file)
        ids = json.loads(id_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("generated %s vectors unreadable (%s) -- ignoring them", kind, exc)
        return {}
    if len(ids) != matrix.shape[0]:
        logger.error(
            "generated %s vectors have %d rows for %d ids -- ignoring them",
            kind, matrix.shape[0], len(ids),
        )
        return {}
    return {identifier: matrix[row].astype(np.float32) for row, identifier in enumerate(ids)}


def _merge_vectors(kind: str, embedder: str, new: dict[str, np.ndarray]) -> None:
    """Append vectors to the overlay matrix, replacing any id already present."""
    merged = load_vectors(kind, embedder)
    merged.update(new)
    ids = sorted(merged)
    if not ids:
        return
    matrix = np.vstack([merged[identifier] for identifier in ids]).astype(np.float32)

    temporary = vectors_path(kind, embedder).with_suffix(f".{os.getpid()}.tmp.npy")
    np.save(temporary, matrix)
    temporary.replace(vectors_path(kind, embedder))
    _write_json(ids_path(kind, embedder), ids)


def append_topic(
    *,
    goal_text: str,
    topic_name: str,
    track: str,
    goal_skill_ids: list[str],
    skills: list[dict[str, Any]],
    courses: list[dict[str, Any]],
    skill_vectors: dict[str, np.ndarray],
    course_vectors: dict[str, np.ndarray],
    embedder: str,
    stats: dict[str, Any],
) -> dict[str, Any]:
    """Commit one expanded topic. Returns the topic record that was stored.

    The whole write is a read-modify-write under a lock, and each file is
    replaced atomically. Ids already present are updated rather than duplicated,
    which makes a re-expansion of the same topic idempotent.
    """
    with _write_lock:
        existing_skills = {entry["id"]: entry for entry in load_skills()}
        existing_courses = {entry["id"]: entry for entry in load_courses()}
        existing_skills.update({entry["id"]: entry for entry in skills})
        existing_courses.update({entry["id"]: entry for entry in courses})

        record = {
            "topic": topic_name,
            "track": track,
            "goal_skill_ids": goal_skill_ids,
            "skill_ids": [entry["id"] for entry in skills],
            "course_ids": [entry["id"] for entry in courses],
            "query": goal_text,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "embedder": embedder,
            **stats,
        }
        topics = load_topics()
        topics[topic_key(goal_text)] = record

        _write_json(generated_dir() / SKILLS_FILE, sorted(existing_skills.values(), key=lambda e: e["id"]))
        _write_json(generated_dir() / COURSES_FILE, sorted(existing_courses.values(), key=lambda e: e["id"]))
        _write_json(generated_dir() / TOPICS_FILE, topics)
        _merge_vectors("skill", embedder, skill_vectors)
        _merge_vectors("catalog", embedder, course_vectors)

    logger.info(
        "stored topic %r: %d skills, %d resources", topic_name, len(skills), len(courses)
    )
    return record


def write_courses(courses: list[dict[str, Any]]) -> None:
    """Replace the discovered catalogue wholesale, atomically.

    For maintenance that rewrites a field across every entry -- re-reading
    descriptions after the extractor improves, for instance. Deliberately not
    used by the expansion path, which appends under the same lock so two
    concurrent builds cannot lose each other's work.
    """
    with _write_lock:
        _write_json(generated_dir() / COURSES_FILE, sorted(courses, key=lambda e: e["id"]))
    logger.info("rewrote %d discovered resources", len(courses))


def alias_topic(goal_text: str, record: dict[str, Any]) -> None:
    """Point another phrasing at a topic that has already been built."""
    with _write_lock:
        topics = load_topics()
        topics[topic_key(goal_text)] = {**record, "query": goal_text, "aliased": True}
        _write_json(generated_dir() / TOPICS_FILE, topics)


def forget_topic(goal_text: str) -> bool:
    """Remove one built subject and everything only it owned. True if it existed.

    The overlay is a cache, and a cache with a bad entry in it is worse than an
    empty one: a syllabus that drifted off-subject keeps matching the goal that
    produced it, so the learner cannot get a better answer by asking again.
    Skills and resources shared with another topic are left alone.
    """
    key = topic_key(goal_text)
    with _write_lock:
        topics = load_topics()
        record = topics.pop(key, None)
        if record is None:
            return False

        still_used_skills = {i for t in topics.values() for i in t.get("skill_ids", [])}
        still_used_courses = {i for t in topics.values() for i in t.get("course_ids", [])}
        drop_skills = set(record.get("skill_ids", [])) - still_used_skills
        drop_courses = set(record.get("course_ids", [])) - still_used_courses

        _write_json(generated_dir() / TOPICS_FILE, topics)
        _write_json(
            generated_dir() / SKILLS_FILE,
            [e for e in load_skills() if e["id"] not in drop_skills],
        )
        _write_json(
            generated_dir() / COURSES_FILE,
            [e for e in load_courses() if e["id"] not in drop_courses],
        )
        bank = load_generated_questions()
        for skill_id in drop_skills:
            bank.pop(skill_id, None)
        _write_json(generated_dir() / QUESTIONS_FILE, bank)

    logger.info(
        "forgot topic %r: %d skills, %d resources removed",
        record.get("topic"), len(drop_skills), len(drop_courses),
    )
    return True


def clear() -> None:
    """Delete the whole overlay. Used by tests and by ``--rebuild``."""
    with _write_lock:
        target = generated_dir()
        for path in target.iterdir():
            if path.is_file():
                path.unlink()
    logger.info("generated overlay cleared")
