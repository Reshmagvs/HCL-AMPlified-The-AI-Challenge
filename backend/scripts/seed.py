"""Prepare everything the app needs before it serves a request.

Creates the database schema and warms the read-only data layers. Idempotent by
construction: ``create_all`` only creates missing tables, so this runs on every
boot -- including inside the container, where the filesystem is wiped on each
rebuild and there is no migration step to rely on.

Warming the embedding model here is deliberate. The local model is downloaded
once (~130 MB) and cached; doing that during a visible "preparing" step is much
better than a mysterious pause on the learner's first question.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core import questions, retrieval, skill_graph  # noqa: E402
from app.core.embeddings import get_embedder  # noqa: E402
from app.db import init_db  # noqa: E402
from app.llm import get_provider  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

logger = logging.getLogger("seed")


def main() -> int:
    """Create tables, load the data files, warm the model, log an inventory."""
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()

    graph = skill_graph.load_graph()
    catalog = retrieval.load_catalog()
    bank = questions.load_questions()

    started = time.perf_counter()
    embedder = get_embedder()
    embedder.embed_batch(["warm up"])
    warm = time.perf_counter() - started

    # Load the language model into memory now. A cold load of a 3B model took
    # 68 seconds on the development machine, and Ollama unloads it after five
    # idle minutes -- so without this the first learner to ask for a new subject
    # waits out the load with no idea why.
    provider = get_provider()
    if hasattr(provider, "warm"):
        provider.warm()

    matrices = retrieval.load_matrices()
    logger.info(
        "ready: %d skills, %d tracks, %d resources, %d questions, "
        "embedder=%s (%.1fs), text=%s%s",
        len(graph), len(graph.tracks), len(catalog), len(bank),
        embedder.name, warm, provider.name,
        "" if provider.available() else " (unavailable -- new subjects cannot be built)",
    )

    if not graph:
        logger.warning("skills.json is empty or missing -- the planner will return nothing")
    if matrices["skills"] is None:
        logger.warning(
            "no embedding matrix for %s -- run: python -m scripts.build_embeddings",
            embedder.name,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
