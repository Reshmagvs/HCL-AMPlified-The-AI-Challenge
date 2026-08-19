"""Precompute the embedding matrices committed to the repository.

Embedding 426 catalog entries and 152 skill nodes on every boot would make a
fresh clone slow. Instead both matrices are built once, L2-normalised, and
written as ``.npy`` files that ship with the repo. At request time only the
learner's goal text is ever new, and even that is embedded locally.

The filename names the model that produced it, so a matrix built with one
embedder can never be silently compared against a query from another --
``core.retrieval`` checks both the row count and the dimension before using one.

    python -m scripts.build_embeddings          # active embedder
    python -m scripts.build_embeddings --both   # local model and offline fallback
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.embeddings import Embedder, FastEmbedder, HashingEmbedder  # noqa: E402
from app.core.embeddings import get_embedder, reset_embedder  # noqa: E402
from app.core.retrieval import load_catalog, matrix_path, reset_caches  # noqa: E402
from app.core.skill_graph import load_graph  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

logger = logging.getLogger("embeddings")

BATCH_SIZE = 64


def embed_texts(embedder: Embedder, texts: list[str], label: str) -> np.ndarray:
    """Embed in batches and fail loudly rather than write half a matrix."""
    chunks: list[np.ndarray] = []
    started = time.perf_counter()

    for start in range(0, len(texts), BATCH_SIZE):
        chunks.append(embedder.embed_batch(texts[start : start + BATCH_SIZE]))
        logger.info("%s: %d/%d", label, min(start + BATCH_SIZE, len(texts)), len(texts))

    matrix = np.vstack(chunks) if chunks else np.zeros((0, embedder.dim), dtype=np.float32)
    if not np.isfinite(matrix).all():
        raise ValueError(f"{label} embeddings contain NaN or inf")

    norms = np.linalg.norm(matrix, axis=1)
    if matrix.size and float(np.abs(norms - 1.0).max()) > 1e-3:
        raise ValueError(f"{label} embeddings are not unit length")

    logger.info("%s: %d vectors in %.1fs", label, len(matrix), time.perf_counter() - started)
    return matrix


def build_for(embedder: Embedder) -> tuple[int, int]:
    """Write both matrices for one embedder. Returns (catalog rows, skill rows)."""
    catalog = load_catalog()
    graph = load_graph()
    skill_ids = sorted(graph.nodes)

    if catalog:
        matrix = embed_texts(embedder, [r.embed_text for r in catalog], "catalog")
        np.save(matrix_path("catalog", embedder.name), matrix)
    else:
        logger.warning("catalog is empty -- skipping catalog embeddings")

    skills = embed_texts(embedder, [graph.nodes[s].embed_text for s in skill_ids], "skills")
    np.save(matrix_path("skill", embedder.name), skills)
    return len(catalog), len(skill_ids)


def main() -> int:
    parser = argparse.ArgumentParser(description="Precompute embedding matrices.")
    parser.add_argument("--both", action="store_true",
                        help="also build the offline hashing fallback")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    reset_caches()
    reset_embedder()

    embedders: list[Embedder] = [get_embedder()]
    if args.both and not isinstance(embedders[0], HashingEmbedder):
        embedders.append(HashingEmbedder())
    if args.both and isinstance(embedders[0], HashingEmbedder):
        logger.warning("the local model is unavailable, so only the fallback was built")

    for embedder in embedders:
        rows, skills = build_for(embedder)
        logger.info("%s: %d catalog rows, %d skill rows, %d dims -> %s",
                    embedder.name, rows, skills, embedder.dim, settings.data_dir)
    del FastEmbedder  # imported for the type union only
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
