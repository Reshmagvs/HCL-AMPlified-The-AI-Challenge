"""Offline evaluation harness -> EVAL_RESULTS.md.

Almost nobody in a student hackathon evaluates their recommender, and "AI/ML
implementation" is 20% of the score. This runs the planner against twenty
synthetic personas with hand-written gold paths and reports seven metrics
against fixed targets.

The targets are stated up front and **never adjusted to match a result**. A
missed target is printed as FAIL, written to the report as FAIL, and said out
loud in the status line -- a metric moved to fit the number it measures is worse
than no metric at all.

    python -m scripts.evaluate
    python -m scripts.evaluate --latency-runs 50
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The latency section exercises real endpoints, which write learner rows. Point
# them at a scratch database so running the harness never pollutes the developer's
# own data. Set before any app import, because settings are cached per process.
_SCRATCH_DB = Path(__file__).resolve().parent.parent / "data" / "_eval_scratch.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_SCRATCH_DB.as_posix()}"
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ["GEMINI_API_KEY"] = ""  # the harness must never make a model call

from app.config import REPO_DIR, get_settings  # noqa: E402
from app.core.mastery import MasteryTable  # noqa: E402
from app.core.planner import build_plan  # noqa: E402
from app.core.retrieval import Preferences, catalog_index  # noqa: E402
from app.core.skill_graph import SkillGraph, load_graph  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

logger = logging.getLogger("evaluate")


@dataclass
class Metric:
    """One measured number and the target it is judged against."""

    name: str
    value: float
    target: str
    passed: bool
    unit: str = ""
    note: str = ""

    @property
    def verdict(self) -> str:
        return "PASS" if self.passed else "FAIL"

    def format_value(self) -> str:
        return f"{self.value:.1f}{self.unit}" if self.unit != "x" else f"{self.value:.2f}x"


def load_personas() -> list[dict[str, Any]]:
    path = get_settings().data_dir / "personas.json"
    if not path.exists():
        raise SystemExit("personas.json missing -- run: python -m scripts.build_personas")
    return json.loads(path.read_text(encoding="utf-8"))


def plan_for_persona(graph: SkillGraph, persona: dict[str, Any]):
    """Reproduce what the API would build for this learner, with no LLM."""
    mastery = MasteryTable()
    for skill_id in persona["known_skills"]:
        mastery.set(skill_id, 1.0, "diagnostic")

    return build_plan(
        graph=graph,
        mastery=mastery,
        goal_ids=persona["goal_node_ids"],
        goal_label=", ".join(persona["goal_node_ids"]),
        prefs=Preferences(
            format_pref=persona["format_pref"],
            cost_pref=persona["cost_pref"],
            language=persona["language"],
            low_bandwidth=persona["low_bandwidth"],
            experience_level=persona["experience_level"],
        ),
        hours_per_week=persona["hours_per_week"],
    )


# --------------------------------------------------------------------------- #
# Quality metrics
# --------------------------------------------------------------------------- #
def evaluate_quality(graph: SkillGraph) -> tuple[list[Metric], list[dict[str, Any]]]:
    """Run every persona and aggregate the seven quality metrics."""
    personas = load_personas()
    catalog = catalog_index()

    violations = steps_total = redundant = 0
    coverage_hits = coverage_expected = 0
    grounded = grounded_total = 0
    paid_leaks = free_only_bound = 0
    ratios: list[float] = []
    rows: list[dict[str, Any]] = []

    for persona in personas:
        plan = plan_for_persona(graph, persona)
        skills = [i.skill_id for i in plan.items if i.kind == "resource"]
        position = {skill: index for index, skill in enumerate(skills)}
        week = {i.skill_id: i.week_number for i in plan.items if i.kind == "resource"}

        local_violations = sum(
            1
            for skill in skills
            for prereq in graph.require(skill).prerequisites
            if prereq in position
            and (position[prereq] > position[skill] or week[prereq] > week[skill])
        )
        violations += local_violations
        steps_total += len(skills)
        redundant += len(skills) - len(set(skills))

        covered = set(skills) | set(persona["known_skills"])
        hits = sum(1 for skill in persona["must_cover"] if skill in covered)
        coverage_hits += hits
        coverage_expected += len(persona["must_cover"])

        for item in plan.items:
            for resource_id in filter(None, [item.course_id, *item.alternatives]):
                grounded_total += 1
                if resource_id in catalog:
                    grounded += 1
                if persona["cost_pref"] == "free":
                    free_only_bound += 1
                    if catalog[resource_id].cost != "free":
                        paid_leaks += 1

        ratio = len(skills) / max(1, persona["gold_path_length"])
        ratios.append(ratio)
        rows.append(
            {
                "persona": persona["id"],
                "steps": len(skills),
                "gold": persona["gold_path_length"],
                "ratio": round(ratio, 2),
                "finish_week": plan.finish_week,
                "hours": plan.total_hours,
                "violations": local_violations,
                "must_cover": f"{hits}/{len(persona['must_cover'])}",
                "unbound": len(plan.unbound_skills),
            }
        )

    within_band = sum(1 for r in ratios if 0.8 <= r <= 1.3)
    metrics = [
        Metric("Prerequisite-order violations", 100.0 * violations / max(1, steps_total),
               "0%", violations == 0, "%",
               f"{violations} across {steps_total} scheduled steps"),
        Metric("Goal-skill coverage", 100.0 * coverage_hits / max(1, coverage_expected),
               ">=95%", coverage_hits / max(1, coverage_expected) >= 0.95, "%",
               f"{coverage_hits}/{coverage_expected} must-cover skills present"),
        Metric("Redundancy (repeated skills)", 100.0 * redundant / max(1, steps_total),
               "0%", redundant == 0, "%", f"{redundant} duplicated steps"),
        Metric("Path length vs gold (mean)", statistics.mean(ratios),
               "0.80-1.30x", 0.8 <= statistics.mean(ratios) <= 1.3, "x",
               f"{within_band}/{len(ratios)} personas individually inside the band"),
        Metric("Free-only compliance", 100.0 if paid_leaks == 0 else
               100.0 * (1 - paid_leaks / max(1, free_only_bound)),
               "100%", paid_leaks == 0, "%",
               f"{paid_leaks} paid resources across {free_only_bound} bindings for free-only learners"),
        Metric("Grounding (resources in catalog)", 100.0 * grounded / max(1, grounded_total),
               "100%", grounded == grounded_total, "%",
               f"{grounded}/{grounded_total} bound ids resolve to a catalog entry"),
    ]
    return metrics, rows


# --------------------------------------------------------------------------- #
# Latency
# --------------------------------------------------------------------------- #
def _time(fn, runs: int) -> dict[str, float]:
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    return {
        "runs": runs,
        "mean_ms": round(statistics.mean(samples), 2),
        "p50_ms": round(samples[len(samples) // 2], 2),
        "p95_ms": round(samples[min(len(samples) - 1, int(0.95 * len(samples)))], 2),
        "max_ms": round(samples[-1], 2),
    }


def measure_latency(graph: SkillGraph, runs: int) -> tuple[dict[str, dict[str, float]], Metric]:
    """Benchmark the paths a request actually takes, with no model involved."""
    from fastapi.testclient import TestClient

    from app.core import retrieval
    from app.main import app

    personas = load_personas()
    heavy = next(p for p in personas if p["id"] == "ml_absolute_beginner")

    def cold_plan() -> None:
        retrieval.reset_caches()
        plan_for_persona(graph, heavy)

    results = {"path_generation_warm": _time(lambda: plan_for_persona(graph, heavy), runs)}
    results["path_generation_cold"] = _time(cold_plan, max(5, runs // 10))
    retrieval.reset_caches()

    with TestClient(app) as client:
        results["health"] = _time(lambda: client.get("/health"), runs)
        learner_id = client.post(
            "/api/intake/commit",
            json={"profile": {"goal_text": "become a machine learning engineer",
                              "hours_per_week": 6}},
        ).json()["learner_id"]
        client.post(f"/api/path/generate/{learner_id}")
        results["diagnostic_next"] = _time(
            lambda: client.get(f"/api/diagnostic/next/{learner_id}"), max(5, runs // 5)
        )
        results["dashboard"] = _time(lambda: client.get(f"/api/dashboard/{learner_id}"), runs)
        results["path_fetch"] = _time(lambda: client.get(f"/api/path/{learner_id}"), runs)

    p95 = results["path_generation_warm"]["p95_ms"]
    metric = Metric("p95 warm path generation", p95, "<2000ms", p95 < 2000, "ms",
                    f"over {runs} runs, no model calls")
    return results, metric


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def write_report(metrics: list[Metric], rows: list[dict[str, Any]],
                 latency: dict[str, dict[str, float]], graph: SkillGraph) -> Path:
    catalog = catalog_index()
    failures = [m for m in metrics if not m.passed]

    lines = [
        "# Evaluation results",
        "",
        "Generated by `python -m scripts.evaluate` against the twenty synthetic",
        "personas in `backend/data/personas.json`. Every number below is measured,",
        "not asserted. Targets are fixed in the harness and are never adjusted to",
        "match a result.",
        "",
        f"Graph: **{len(graph)} skills**, {len(graph.tracks)} tracks. ",
        f"Catalog: **{len(catalog)} verified resources**. ",
        "Provider: none -- the whole harness runs deterministically with no model.",
        "",
        "## Quality",
        "",
        "| Metric | Result | Target | Verdict | Detail |",
        "|---|---|---|---|---|",
    ]
    for metric in metrics:
        lines.append(
            f"| {metric.name} | **{metric.format_value()}** | {metric.target} "
            f"| {metric.verdict} | {metric.note} |"
        )

    lines += [
        "",
        "## Per-persona detail",
        "",
        "| Persona | Steps | Gold | Ratio | Finish week | Hours | Violations | Must-cover | Unbound |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['persona']}` | {row['steps']} | {row['gold']} | {row['ratio']}x "
            f"| {row['finish_week']} | {row['hours']} | {row['violations']} "
            f"| {row['must_cover']} | {row['unbound']} |"
        )

    lines += ["", "## Latency", "",
              "Measured on the build machine with a warm process and no model calls.",
              "", "| Operation | Runs | Mean | p50 | p95 | Max |", "|---|---|---|---|---|---|"]
    for name, stats in latency.items():
        lines.append(
            f"| {name.replace('_', ' ')} | {stats['runs']} | {stats['mean_ms']}ms "
            f"| {stats['p50_ms']}ms | {stats['p95_ms']}ms | {stats['max_ms']}ms |"
        )

    lines += ["", "## Verdict", ""]
    if failures:
        lines.append(f"**{len(failures)} metric(s) missed their target.**")
        lines += [f"- {m.name}: {m.format_value()} against a target of {m.target}. {m.note}"
                  for m in failures]
    else:
        lines.append("**Every metric met its target.**")
    lines.append("")

    target = REPO_DIR / "EVAL_RESULTS.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline evaluation harness.")
    parser.add_argument("--latency-runs", type=int, default=50)
    args = parser.parse_args()

    configure_logging("WARNING")
    graph = load_graph()
    if not graph:
        raise SystemExit("no skill graph -- run: python -m scripts.build_skills")

    quality, rows = evaluate_quality(graph)
    latency, latency_metric = measure_latency(graph, args.latency_runs)
    metrics = [*quality, latency_metric]
    path = write_report(metrics, rows, latency, graph)

    print()
    print("EVALUATION")
    print("=" * 78)
    for metric in metrics:
        print(f"  [{metric.verdict}] {metric.name:<34} {metric.format_value():>10}"
              f"   target {metric.target}")
    print()
    print(f"  report written to {path}")
    failures = [m for m in metrics if not m.passed]
    if failures:
        print(f"  !! {len(failures)} metric(s) MISSED their target: "
              + ", ".join(m.name for m in failures))
    print()

    from app.db import engine

    engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        Path(str(_SCRATCH_DB) + suffix).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
