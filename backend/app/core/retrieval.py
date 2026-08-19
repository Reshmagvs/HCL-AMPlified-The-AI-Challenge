"""Catalog retrieval and resource scoring.

Two matrices are loaded once at startup and never rebuilt at request time:
``catalog_embeddings.npy`` (one row per catalog entry) and
``skill_embeddings.npy`` (one row per graph node). Both are L2-normalised when
built, which turns cosine similarity into a single matrix-vector product. At
roughly 500 catalog rows this is microseconds -- a vector database would be
strictly slower here, and it would add a service to deploy for no benefit.

Scoring is deliberately two-staged:

**Hard filters run first.** ``free_only``, language and low-bandwidth are
constraints, not preferences. Folding them into the weighted sum would let a
sufficiently good paid course outscore a mediocre free one, which is exactly the
failure a learner who cannot pay experiences as the product lying to them. So
non-conforming items are removed from the candidate set before any arithmetic.

**Then a weighted sum over the survivors**::

    0.45 * cosine(skill, resource)   semantic fit to the skill node
  + 0.20 * level_match               beginner content for a foundational node
  + 0.15 * format_pref               video vs text vs interactive
  + 0.10 * cost_pref                 free still wins ties when paid is allowed
  + 0.10 * rating

The top three survive: rank 1 is bound to the path item, ranks 2 and 3 become
the swap options and the "chosen over N alternatives" line in the provenance.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)

WEIGHT_COSINE = 0.45
WEIGHT_LEVEL = 0.20
WEIGHT_FORMAT = 0.15
WEIGHT_COST = 0.10
WEIGHT_RATING = 0.10

# A resource that does not credibly cover the skill is filtered out before any
# score is computed: "Data Structures and Algorithms" bound to Linux
# Administration is wrong however good the resource is.
#
# The rule is *relative* rather than an absolute cosine threshold, because
# absolute similarity is model-specific -- the same correct pairing scores 0.82
# under one embedder and 0.14 under another. Within one skill candidate set the
# ordering is stable across models, so a candidate is kept when it lands within
# RELEVANCE_MARGIN of the best candidate for that skill.
RELEVANCE_MARGIN = 0.10

_LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}
_VIDEO_FORMATS = {"video"}


@dataclass(frozen=True)
class Resource:
    """One catalog entry. Every field is verified before it lands in the file."""

    id: str
    title: str
    provider: str
    url: str
    format: str
    cost: str
    duration_hours: float
    level: str
    skills_covered: tuple[str, ...]
    rating: float
    language: str
    description: str

    @property
    def embed_text(self) -> str:
        return f"{self.title}. {self.description} Provider: {self.provider}. Format: {self.format}."

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "provider": self.provider,
            "url": self.url,
            "format": self.format,
            "cost": self.cost,
            "duration_hours": self.duration_hours,
            "level": self.level,
            "rating": self.rating,
            "description": self.description,
        }


@dataclass(frozen=True)
class Preferences:
    """The learner constraints that shape binding."""

    format_pref: str = "any"
    cost_pref: str = "any"
    language: str = "en"
    low_bandwidth: bool = False
    experience_level: str = "beginner"


@dataclass(frozen=True)
class ScoredResource:
    """A candidate with its total score and the component breakdown."""

    resource: Resource
    score: float
    components: dict[str, float]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def load_catalog() -> list[Resource]:
    """Read data/courses.json. An absent file yields an empty catalog, not a crash."""
    target = get_settings().data_dir / "courses.json"
    if not target.exists():
        logger.warning("courses.json not found at %s -- serving an empty catalog", target)
        return []
    raw = json.loads(target.read_text(encoding="utf-8"))
    catalog = [
        Resource(
            id=entry["id"],
            title=entry["title"],
            provider=entry["provider"],
            url=entry["url"],
            format=entry["format"],
            cost=entry["cost"],
            duration_hours=float(entry["duration_hours"]),
            level=entry["level"],
            skills_covered=tuple(entry["skills_covered"]),
            rating=float(entry.get("rating", 4.0)),
            language=entry.get("language", "en"),
            description=entry.get("description", ""),
        )
        for entry in raw
    ]
    logger.info("catalog loaded: %d resources", len(catalog))
    return catalog


@lru_cache(maxsize=1)
def catalog_index() -> dict[str, Resource]:
    """id -> resource, for O(1) lookup when rendering a stored path."""
    return {r.id: r for r in load_catalog()}


@lru_cache(maxsize=1)
def skills_by_resource() -> dict[str, list[Resource]]:
    """skill id -> every resource that covers it, in stable catalog order."""
    index: dict[str, list[Resource]] = {}
    for resource in load_catalog():
        for skill_id in resource.skills_covered:
            index.setdefault(skill_id, []).append(resource)
    return index


def matrix_path(kind: str, embedder: str) -> Path:
    """Where one embedder's matrix lives.

    Vectors from two different models are not comparable, so the filename names
    the model that produced them and ``load_matrices`` refuses any matrix whose
    shape disagrees with the active embedder. A silent mismatch would surface as
    quietly terrible recommendations rather than as an error.
    """
    return get_settings().data_dir / f"{kind}_embeddings.{embedder}.npy"


def _load_matrix(
    path: Path, expected_rows: int, expected_dim: int, label: str
) -> np.ndarray | None:
    if not path.exists():
        logger.warning(
            "%s embeddings missing at %s -- run: python -m scripts.build_embeddings",
            label, path,
        )
        return None
    matrix = np.load(path)
    if matrix.shape[0] != expected_rows or matrix.shape[1] != expected_dim:
        logger.error(
            "%s embeddings are %s but %d rows of %d dims are expected -- rebuild them",
            label, matrix.shape, expected_rows, expected_dim,
        )
        return None
    return matrix.astype(np.float32)


@lru_cache(maxsize=1)
def load_matrices() -> dict[str, Any]:
    """Load both embedding matrices plus the id -> row index maps."""
    from app.core.skill_graph import load_graph

    from app.core.embeddings import get_embedder

    catalog = load_catalog()
    skill_ids = sorted(load_graph().nodes)
    embedder = get_embedder()

    return {
        "embedder": embedder.name,
        "dim": embedder.dim,
        "catalog": _load_matrix(
            matrix_path("catalog", embedder.name), len(catalog), embedder.dim, "catalog"
        ),
        "catalog_ids": [r.id for r in catalog],
        "catalog_row": {r.id: i for i, r in enumerate(catalog)},
        "skills": _load_matrix(
            matrix_path("skill", embedder.name), len(skill_ids), embedder.dim, "skill"
        ),
        "skill_ids": skill_ids,
        "skill_row": {s: i for i, s in enumerate(skill_ids)},
    }


def reset_caches() -> None:
    """Drop every cached read of the data directory. Used by scripts and tests."""
    for fn in (load_catalog, catalog_index, skills_by_resource, load_matrices):
        fn.cache_clear()


# --------------------------------------------------------------------------- #
# Similarity
# --------------------------------------------------------------------------- #
def normalize(vector: np.ndarray) -> np.ndarray:
    """L2-normalise, tolerating the zero vector."""
    norm = float(np.linalg.norm(vector))
    return vector if norm == 0.0 else vector / norm


def cosine_search(query: np.ndarray, matrix: np.ndarray, top_k: int) -> list[tuple[int, float]]:
    """Top-k rows of an already-normalised matrix by cosine, as (row, score)."""
    if matrix is None or matrix.size == 0:
        return []
    scores = matrix @ normalize(query.astype(np.float32))
    k = min(top_k, scores.shape[0])
    top = np.argpartition(-scores, k - 1)[:k]
    ordered = top[np.argsort(-scores[top], kind="stable")]
    return [(int(i), float(scores[i])) for i in ordered]


def skill_vector(skill_id: str) -> np.ndarray | None:
    """The precomputed embedding row for a skill node, if it exists."""
    matrices = load_matrices()
    row = matrices["skill_row"].get(skill_id)
    if row is None or matrices["skills"] is None:
        return None
    return matrices["skills"][row]


def resource_vector(resource_id: str) -> np.ndarray | None:
    matrices = load_matrices()
    row = matrices["catalog_row"].get(resource_id)
    if row is None or matrices["catalog"] is None:
        return None
    return matrices["catalog"][row]


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def expected_level(difficulty: int) -> str:
    """Map a node difficulty (1-5) onto the level of content that suits it."""
    if difficulty <= 2:
        return "beginner"
    return "intermediate" if difficulty == 3 else "advanced"


def _level_match(resource_level: str, target_level: str) -> float:
    a = _LEVEL_ORDER.get(resource_level, 1)
    b = _LEVEL_ORDER.get(target_level, 1)
    return {0: 1.0, 1: 0.5}.get(abs(a - b), 0.0)


def _format_match(resource_format: str, pref: str) -> float:
    if pref in ("", "any"):
        return 0.6
    if resource_format == pref:
        return 1.0
    return 0.8 if {resource_format, pref} <= {"interactive", "course"} else 0.15


def _relative_score(raw: float | None, measured: list[float]) -> float:
    """Position within this candidate set, not an absolute similarity."""
    if raw is None or not measured:
        return 0.5
    return max(0.0, min(1.0, 1.0 - (max(measured) - raw) / RELEVANCE_MARGIN))


def _relevance(query: np.ndarray | None, vector: np.ndarray | None) -> float | None:
    """Cosine between a skill and a resource, or None when either is unembedded."""
    if query is None or vector is None:
        return None
    return float(np.dot(query, vector))


def passes_hard_filters(resource: Resource, prefs: Preferences) -> bool:
    """Constraints, not preferences. Applied before any score is computed."""
    if prefs.cost_pref == "free" and resource.cost != "free":
        return False
    if prefs.language and resource.language != prefs.language:
        return False
    if prefs.low_bandwidth and resource.format in _VIDEO_FORMATS:
        return False
    return True


def score_resources(
    skill_id: str,
    difficulty: int,
    prefs: Preferences,
    *,
    top_k: int = 3,
    exclude: set[str] | None = None,
) -> list[ScoredResource]:
    """Rank the resources covering one skill. Hard filters first, then weights."""
    excluded = exclude or set()
    candidates = [
        r
        for r in skills_by_resource().get(skill_id, [])
        if r.id not in excluded and passes_hard_filters(r, prefs)
    ]
    if not candidates:
        return []

    query = skill_vector(skill_id)
    target_level = expected_level(difficulty)

    relevance = {r.id: _relevance(query, resource_vector(r.id)) for r in candidates}
    measured = [v for v in relevance.values() if v is not None]
    if measured:
        cutoff = max(measured) - RELEVANCE_MARGIN
        # An imperfect resource still beats an empty step, so the best available
        # survives even when nothing is a strong match.
        candidates = [
            r for r in candidates if relevance[r.id] is None or relevance[r.id] >= cutoff
        ] or candidates

    scored: list[ScoredResource] = []
    for resource in candidates:
        raw = relevance[resource.id]
        components = {
            # Scored by position within this candidate set, for the same reason
            # the filter is relative: absolute cosine is not comparable across
            # embedding models, but the gap inside one set is. The best match
            # becomes 1.0 and RELEVANCE_MARGIN behind it becomes 0.0.
            "cosine": _relative_score(raw, measured),
            "level": _level_match(resource.level, target_level),
            "format": _format_match(resource.format, prefs.format_pref),
            "cost": 1.0 if resource.cost == "free" else 0.25,
            "rating": max(0.0, min(1.0, (resource.rating - 3.0) / 2.0)),
        }
        total = (
            WEIGHT_COSINE * components["cosine"]
            + WEIGHT_LEVEL * components["level"]
            + WEIGHT_FORMAT * components["format"]
            + WEIGHT_COST * components["cost"]
            + WEIGHT_RATING * components["rating"]
        )
        scored.append(ScoredResource(resource=resource, score=round(total, 6), components=components))

    # Ties break on id so binding is byte-identical across runs.
    scored.sort(key=lambda s: (-s.score, s.resource.id))
    return scored[:top_k]
