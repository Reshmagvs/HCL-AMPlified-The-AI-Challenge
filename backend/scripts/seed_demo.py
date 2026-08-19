"""Create a fully populated demo learner, so nothing is typed on camera.

Recording a demo by typing into a form wastes thirty seconds of a four-minute
budget and invites a typo on take five. This builds the state a learner would
have after a real session -- profile, answered diagnostic, generated path,
several completed steps and one adaptation event -- in about two seconds and
with no model calls.

    python -m scripts.seed_demo              # default ML-engineer learner
    python -m scripts.seed_demo --persona da_sql_experienced --reset
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.mastery import MasteryTable, MasteryValue  # noqa: E402
from app.core.skill_graph import load_graph  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.models import Event, Learner, LearningPath, Mastery, PathItem, QuizItem  # noqa: E402
from app.pathing import load_items, persist_plan, plan_for  # noqa: E402

logger = logging.getLogger("seed_demo")

DEFAULT_PERSONA = "ml_cs_student"
DEMO_NAME = "Demo Learner"


def _persona(persona_id: str) -> dict:
    path = get_settings().data_dir / "personas.json"
    personas = json.loads(path.read_text(encoding="utf-8"))
    for persona in personas:
        if persona["id"] == persona_id:
            return persona
    raise SystemExit(
        f"unknown persona {persona_id!r}; known: {', '.join(p['id'] for p in personas)}"
    )


def _wipe_existing(db: Session) -> None:
    """Remove any previous demo learner so re-running is idempotent."""
    for learner in db.exec(select(Learner).where(Learner.display_name == DEMO_NAME)).all():
        paths = db.exec(select(LearningPath).where(LearningPath.learner_id == learner.id)).all()
        for path in paths:
            for item in load_items(db, path.id):
                db.delete(item)
            db.delete(path)
        for model in (Mastery, Event, QuizItem):
            for row in db.exec(select(model).where(model.learner_id == learner.id)).all():
                db.delete(row)
        db.delete(learner)
    db.commit()


def _seed_mastery(db: Session, learner_id: int, persona: dict) -> MasteryTable:
    """Write the mastery a completed diagnostic would have produced."""
    table = MasteryTable()
    for skill_id in persona["known_skills"]:
        table.set(skill_id, 1.0, "diagnostic", confidence=1.0)
    for skill_id, value in table.items():
        db.add(
            Mastery(
                learner_id=learner_id, skill_id=skill_id,
                score=value.score, source=value.source, confidence=value.confidence,
            )
        )
    db.add(Event(learner_id=learner_id, type="diagnostic_completed",
                 payload={"measured": len(persona["known_skills"]), "seeded": True}))
    db.commit()
    return MasteryTable({k: MasteryValue(v.score, v.source, v.confidence) for k, v in table.items()})


def _complete_early_items(db: Session, path_id: int, count: int) -> list[str]:
    """Mark the first few steps done, so progress is visible immediately."""
    done: list[str] = []
    for item in load_items(db, path_id)[:count]:
        if item.kind != "resource":
            continue
        item.status = "done"
        db.add(item)
        done.append(item.skill_id)
    db.commit()
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a populated demo learner.")
    parser.add_argument("--persona", default=DEFAULT_PERSONA)
    parser.add_argument("--completed", type=int, default=3, help="steps to mark done")
    parser.add_argument("--reset", action="store_true", help="remove previous demo learners first")
    args = parser.parse_args()

    configure_logging(get_settings().log_level)
    init_db()
    graph = load_graph()
    persona = _persona(args.persona)

    with Session(engine) as db:
        if args.reset:
            _wipe_existing(db)

        learner = Learner(
            display_name=DEMO_NAME,
            goal_text="I want to become a machine learning engineer",
            goal_node_ids=persona["goal_node_ids"],
            interests=["machine learning", "python"],
            experience_level=persona["experience_level"],
            hours_per_week=persona["hours_per_week"],
            format_pref=persona["format_pref"],
            cost_pref=persona["cost_pref"],
            language=persona["language"],
            low_bandwidth=persona["low_bandwidth"],
        )
        db.add(learner)
        db.commit()
        db.refresh(learner)

        db.add(Event(learner_id=learner.id, type="intake_committed",
                     payload={"goal_node_ids": persona["goal_node_ids"], "seeded": True}))
        db.commit()

        mastery = _seed_mastery(db, learner.id, persona)
        plan = plan_for(learner, mastery)
        path, _degraded = persist_plan(db, learner, plan, event_type="path_generated")
        done = _complete_early_items(db, path.id, args.completed)

        for skill_id in done:
            db.add(Event(learner_id=learner.id, type="completed_item",
                         payload={"skill_id": skill_id}))
        db.commit()

        items = load_items(db, path.id)
        logger.info(
            "demo learner %d ready: goal=%s, %d steps, finish week %d, %d marked done",
            learner.id,
            ", ".join(graph.require(g).name for g in learner.goal_node_ids),
            len(items),
            path.finish_week,
            len(done),
        )
        print(f"\n  Demo learner id: {learner.id}")
        print(f"  Open: http://localhost:5173  (set learnerId {learner.id} in the app)")
        print(f"  API:  http://127.0.0.1:8000/api/path/{learner.id}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
