"""Precompute the embedding matrices committed to the repository.

Embedding 500 catalog entries and 152 skill nodes on every boot would make a
fresh clone slow, expensive and dependent on an API key. Instead both matrices
are built once, L2-normalised, and written as ``.npy`` files that ship with the
repo. At request time only the learner's goal text is ever new.

**One file set per provider.** ``MockProvider`` and Gemini produce vectors in
different spaces, so a query embedded by one cannot be compared against a matrix
built by the other. Files are therefore suffixed by provider (`.mock.npy`), and
``core.retrieval`` loads the set matching the active provider. This is what lets
a clone with no API key still resolve goals sensibly instead of comparing noise.

Batched, resumable and idempotent: embeddings are cached by SHA256 of the text,
so re-running after adding twenty catalog entries embeds twenty texts, not five
hundred.

    python -m scripts.build_embeddings            # active provider
    python -m scripts.build_embeddings --both     # gemini and mock
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.retrieval import load_catalog, matrix_path, reset_caches  # noqa: E402
from app.core.skill_graph import load_graph  # noqa: E402
from app.db import init_db  # noqa: E402
from app.llm.base import LLMProvider, ProviderUnavailable  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

logger = logging.getLogger("embeddings")

BATCH_SIZE = 20
FREE_TIER_PER_MINUTE = 55  # stay well inside the 100/min free-tier embed quota
MAX_ATTEMPTS = 15
_RETRY_DELAY_RE = re.compile(r"retry(?:_|\s*)?[dD]elay['\":\s]+(\d+(?:\.\d+)?)s?")


def _retry_delay(message: str, default: float) -> float:
    """Honour the server's own retry hint rather than guessing a backoff."""
    match = _RETRY_DELAY_RE.search(message)
    return min(float(match.group(1)) + 2.0, 90.0) if match else default


def embed_texts(provider: LLMProvider, texts: list[str], label: str) -> np.ndarray:
    """Embed in batches under the rate limit, L2-normalise, never half-write."""
    remote = provider.name != "mock"
    throttle = 60.0 * BATCH_SIZE / FREE_TIER_PER_MINUTE if remote else 0.0
    vectors: list[list[float]] = []

    for start in range(0, len(texts), BATCH_SIZE):
        chunk = texts[start : start + BATCH_SIZE]
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                vectors.extend(provider.embed_batch(chunk))
                break
            except ProviderUnavailable as exc:
                if attempt == MAX_ATTEMPTS:
                    raise
                wait = _retry_delay(str(exc), default=10.0 * attempt)
                logger.warning("%s at %d rate-limited, sleeping %.0fs", label, start, wait)
                time.sleep(wait)
        logger.info("%s: %d/%d embedded", label, len(vectors), len(texts))
        if throttle and start + BATCH_SIZE < len(texts):
            time.sleep(throttle)

    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    matrix = matrix / norms

    if not np.isfinite(matrix).all():
        raise ValueError(f"{label} embeddings contain NaN or inf")
    return matrix


def build_for(provider: LLMProvider) -> tuple[int, int]:
    """Write both matrices for one provider. Returns (catalog rows, skill rows)."""
    catalog = load_catalog()
    graph = load_graph()
    skill_ids = sorted(graph.nodes)

    if catalog:
        matrix = embed_texts(provider, [r.embed_text for r in catalog], "catalog")
        np.save(matrix_path("catalog", provider.name), matrix)
    else:
        logger.warning("catalog is empty -- skipping catalog embeddings")

    skills = embed_texts(provider, [graph.nodes[s].embed_text for s in skill_ids], "skills")
    np.save(matrix_path("skill", provider.name), skills)

    return len(catalog), len(skill_ids)


def main() -> int:
    parser = argparse.ArgumentParser(description="Precompute embedding matrices.")
    parser.add_argument("--both", action="store_true", help="build for gemini and mock")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    # The EmbeddingCache table has to exist before the provider can read or write
    # it. Without this the cache silently no-ops and every run pays full price.
    init_db()
    reset_caches()

    from app.llm import get_provider
    from app.llm.mock import MockProvider

    providers: list[LLMProvider] = [get_provider()]
    if args.both and providers[0].name != "mock":
        providers.append(MockProvider())

    for provider in providers:
        rows, skills = build_for(provider)
        logger.info("%s: %d catalog rows, %d skill rows -> %s",
                    provider.name, rows, skills, settings.data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
