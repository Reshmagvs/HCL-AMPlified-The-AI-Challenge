"""Create the database schema and report what the data layer holds.

Idempotent by construction: ``create_all`` only creates missing tables, so this
runs on every boot -- including inside the Hugging Face container, where the
filesystem is wiped on each rebuild and there is no migration step to rely on.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core import retrieval, skill_graph  # noqa: E402
from app.db import init_db  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

logger = logging.getLogger("seed")


def main() -> int:
    """Create tables, load the data files, and log a one-line inventory."""
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()

    graph = skill_graph.load_graph()
    catalog = retrieval.load_catalog()
    logger.info(
        "seed complete: %d skill nodes, %d tracks, %d catalog resources, provider=%s",
        len(graph),
        len(graph.tracks),
        len(catalog),
        settings.llm_provider,
    )
    if not graph:
        logger.warning("skills.json is empty or missing -- the planner will return nothing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
