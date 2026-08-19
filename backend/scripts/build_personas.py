"""Curation source for ``data/personas.json`` -- the evaluation set.

Twenty synthetic learners across all five tracks, each with a hand-written
**gold path length** and a set of skills a correct plan must cover. They exist
so the recommender can be evaluated rather than merely demonstrated, which is
the difference between "it looks right" and "here is the violation rate".

Each persona is ``(id, goal_ids, hours, known skill ids, prefs, gold length,
must-cover skill ids)``:

* ``known`` is what the persona has already demonstrated -- seeded at full
  mastery, as a diagnostic would.
* ``gold_length`` is a hand-estimated number of steps a competent human planner
  would produce for that learner. The metric is the *ratio* of generated to
  gold, targeted at 0.8-1.3x; being far under means skipping necessary work,
  far over means padding.
* ``must_cover`` names the skills whose absence would make the path wrong,
  regardless of length.

Run ``python -m scripts.build_personas`` from ``backend/`` to regenerate.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.skill_graph import load_graph  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

logger = logging.getLogger("personas")

# Shorthand bundles of prior knowledge, so a persona reads in one line.
PY_BASICS = ["prog.python_basics", "prog.control_flow", "prog.python_data_structures"]
PY_FULL = [*PY_BASICS, "prog.functions", "prog.files_io", "prog.python_oop"]
TOOLING = ["prog.cli", "prog.git"]
MATHS = ["math.algebra", "math.functions_graphs"]
WEB_FRONT = ["web.html", "web.css", "web.javascript", "web.dom", "web.modules", "web.npm"]
WEB_FULL = [*WEB_FRONT, "web.js_async", "web.react", "web.http", "web.typescript"]
SQL = ["data.sql_basics", "data.sql_joins"]
SYS = ["cs.os_basics", "cs.networking"]

PERSONAS: list[tuple[str, list[str], float, list[str], dict[str, Any], int, list[str]]] = [
    # -- machine learning ----------------------------------------------------
    ("ml_absolute_beginner", ["ml.engineer"], 6, [], {}, 44,
     ["prog.python_basics", "math.linear_algebra", "ml.gradient_descent", "ml.engineer"]),
    ("ml_cs_student", ["ml.engineer"], 10, [*PY_FULL, *TOOLING, *MATHS],
     {"cost_pref": "free"}, 36, ["math.derivatives", "ml.backpropagation", "ml.mlops"]),
    ("ml_web_dev_switcher", ["ml.engineer"], 8, [*WEB_FULL, *TOOLING, *PY_BASICS],
     {}, 34, ["math.statistics", "ml.neural_nets", "ml.engineer"]),
    ("ml_deep_learning_focus", ["ml.cnn"], 12, [*PY_FULL, "prog.numpy", *MATHS, "math.derivatives"],
     {"format_pref": "video"}, 18, ["ml.frameworks", "ml.cnn"]),
    ("ml_llm_builder", ["ml.llm_applications"], 15, [*PY_FULL, *TOOLING, *WEB_FRONT],
     {}, 32, ["ml.transformers", "ml.nlp_basics", "ml.llm_applications"]),
    # -- web development -----------------------------------------------------
    ("web_absolute_beginner", ["web.fullstack_engineer"], 8, [], {"cost_pref": "free"}, 32,
     ["web.html", "web.react", "web.auth_sessions", "web.fullstack_engineer"]),
    ("web_frontend_to_fullstack", ["web.fullstack_engineer"], 10, [*WEB_FRONT, *TOOLING],
     {}, 24, ["web.node_backend", "web.orm_persistence", "web.deployment"]),
    ("web_low_bandwidth", ["web.fullstack_engineer"], 5, [*TOOLING],
     {"low_bandwidth": True, "format_pref": "text", "cost_pref": "free"}, 31,
     ["web.html", "web.javascript", "web.rest_api_design"]),
    ("web_react_specialist", ["web.state_management"], 12, [*WEB_FRONT], {}, 6,
     ["web.react_state", "web.react_hooks", "web.state_management"]),
    ("web_accessibility_focus", ["web.accessibility"], 4, [], {}, 4,
     ["web.html", "web.css", "web.accessibility"]),
    # -- data analytics ------------------------------------------------------
    ("da_career_changer", ["da.analyst"], 6, [], {"cost_pref": "free"}, 26,
     ["math.statistics", "da.sql_analytics", "da.dashboards", "da.analyst"]),
    ("da_sql_experienced", ["da.analyst"], 10, [*SQL, *PY_BASICS, "prog.cli"], {}, 22,
     ["math.probability", "da.experiment_design", "da.ab_testing"]),
    ("da_engineer_track", ["da.data_engineer"], 12, [*PY_FULL, *TOOLING, *SQL, *SYS],
     {}, 18, ["da.etl", "da.warehousing", "cloud.docker"]),
    ("da_forecasting", ["da.forecasting"], 8, [*PY_FULL, "prog.numpy", "prog.pandas", *MATHS],
     {}, 12, ["da.time_series", "da.forecasting"]),
    # -- cybersecurity -------------------------------------------------------
    ("sec_blue_team_beginner", ["sec.analyst"], 8, [], {"cost_pref": "free"}, 13,
     ["cs.networking", "sec.fundamentals", "sec.siem", "sec.analyst"]),
    ("sec_red_team", ["sec.pentester"], 12, [*SYS, *TOOLING, *PY_BASICS], {}, 20,
     ["sec.web_vulnerabilities", "sec.exploitation", "sec.pentester"]),
    ("sec_web_appsec", ["sec.secure_coding"], 6, [*WEB_FRONT, "web.http", *PY_FULL], {}, 8,
     ["sec.web_vulnerabilities", "sec.secure_coding"]),
    # -- cloud and devops ----------------------------------------------------
    ("cloud_sysadmin_upskill", ["cloud.devops_engineer"], 10, [*SYS, *TOOLING], {}, 18,
     ["cloud.docker", "cloud.kubernetes", "cloud.ci_cd", "cloud.devops_engineer"]),
    ("cloud_architect_track", ["cloud.architect"], 8, [*SYS, "prog.cli"],
     {"cost_pref": "free"}, 17, ["cloud.iac", "cloud.networking", "sec.cloud_security"]),
    ("cloud_one_hour_a_week", ["cloud.docker"], 1, ["prog.cli"], {}, 4,
     ["cs.os_basics", "cloud.linux_admin", "cloud.docker"]),
]


def to_json() -> list[dict[str, Any]]:
    """Flatten the authored tuples, validating every id against the graph."""
    graph = load_graph()
    records: list[dict[str, Any]] = []

    for persona_id, goals, hours, known, prefs, gold_length, must_cover in PERSONAS:
        for skill_id in [*goals, *known, *must_cover]:
            if skill_id not in graph:
                raise ValueError(f"persona {persona_id} references unknown skill {skill_id!r}")
        records.append(
            {
                "id": persona_id,
                "goal_node_ids": goals,
                "hours_per_week": hours,
                "known_skills": sorted(set(known)),
                "format_pref": prefs.get("format_pref", "any"),
                "cost_pref": prefs.get("cost_pref", "any"),
                "language": prefs.get("language", "en"),
                "low_bandwidth": prefs.get("low_bandwidth", False),
                "experience_level": "beginner" if not known else "intermediate",
                "gold_path_length": gold_length,
                "must_cover": sorted(set(must_cover)),
            }
        )
    return records


def main() -> int:
    configure_logging("INFO")
    records = to_json()
    target = get_settings().data_dir / "personas.json"
    target.write_text(json.dumps(records, indent=1) + "\n", encoding="utf-8")

    tracks = {
        load_graph().require(goal).track
        for record in records
        for goal in record["goal_node_ids"]
    }
    logger.info("wrote %s: %d personas across %d tracks", target, len(records), len(tracks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
