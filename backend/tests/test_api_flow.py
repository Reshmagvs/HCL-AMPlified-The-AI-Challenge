"""End-to-end coverage of every endpoint, plus the Phase 5 and 7 acceptance tests.

The journey here is the one a learner takes: intake, diagnostic, path, adaptation,
dashboard, chat. Running it as one ordered module means each test exercises real
state produced by the previous step rather than a fixture that only resembles it.
"""

from __future__ import annotations

import pytest

from app.core.mastery import SELF_REPORT_CAP


@pytest.fixture(scope="module")
def learner_id(client) -> int:
    """One learner, committed once, shared by every test in this module."""
    response = client.post(
        "/api/intake/commit",
        json={
            "display_name": "Journey",
            "profile": {
                "goal_text": "become a machine learning engineer",
                "hours_per_week": 6,
                "experience_level": "beginner",
                "completed_skills": ["Python Basics", "Version Control with Git"],
                "format_pref": "video",
                "cost_pref": "free",
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["learner_id"]


# --------------------------------------------------------------------------- #
# Diagnostic
# --------------------------------------------------------------------------- #
def test_diagnostic_never_leaks_the_answer_key(client, learner_id) -> None:
    response = client.get(f"/api/diagnostic/next/{learner_id}")
    assert response.status_code == 200
    body = response.json()
    assert "answer_index" not in response.text
    assert "answer" not in {k.lower() for k in body}
    assert body["question"] and len(body["options"]) == 4


def test_diagnostic_terminates_and_records_answers(client, learner_id) -> None:
    """Answer everything correctly; the loop must stop, and mastery must reach 1.0."""
    from app.db import engine
    from sqlmodel import Session, select
    from app.models import QuizItem

    asked = 0
    done = False
    reached_one = False

    while asked < 15:
        body = client.get(f"/api/diagnostic/next/{learner_id}").json()
        if body["done"]:
            done = True
            break
        with Session(engine) as db:
            item = db.get(QuizItem, body["quiz_item_id"])
            key = item.answer_index
        answer = client.post(
            "/api/diagnostic/answer",
            json={"quiz_item_id": body["quiz_item_id"], "chosen_index": key},
        ).json()
        assert answer["correct"] is True
        reached_one = reached_one or answer["new_score"] == 1.0
        asked += 1
        if answer["done"]:
            done = True
            break

    assert done, "the diagnostic must terminate"
    assert reached_one, "a passed question reaches full mastery"
    assert asked <= 10, "DIAGNOSTIC_MAX_QUESTIONS must be honoured"


def test_dont_know_is_recorded_distinctly(client, learner_id) -> None:
    from app.db import engine
    from sqlmodel import Session
    from app.models import QuizItem

    body = client.get(f"/api/diagnostic/next/{learner_id}").json()
    if body["done"]:
        pytest.skip("this learner's diagnostic already terminated")

    result = client.post(
        "/api/diagnostic/answer",
        json={"quiz_item_id": body["quiz_item_id"], "dont_know": True},
    ).json()
    assert result["correct"] is False

    with Session(engine) as db:
        item = db.get(QuizItem, body["quiz_item_id"])
    assert item.dont_know is True
    assert item.chosen_index is None


def test_answering_twice_is_rejected(client, learner_id) -> None:
    from app.db import engine
    from sqlmodel import Session, select
    from app.models import QuizItem

    with Session(engine) as db:
        answered = db.exec(
            select(QuizItem).where(QuizItem.learner_id == learner_id)
        ).all()
    item = next(q for q in answered if q.chosen_index is not None or q.dont_know)
    response = client.post(
        "/api/diagnostic/answer", json={"quiz_item_id": item.id, "chosen_index": 0}
    )
    assert response.status_code == 409


def test_self_report_rows_never_exceed_the_cap(client, learner_id) -> None:
    from app.db import engine
    from sqlmodel import Session, select
    from app.models import Mastery

    with Session(engine) as db:
        rows = db.exec(select(Mastery).where(Mastery.learner_id == learner_id)).all()
    for row in rows:
        if row.source == "self":
            assert row.score <= SELF_REPORT_CAP


# --------------------------------------------------------------------------- #
# Path
# --------------------------------------------------------------------------- #
def test_generate_and_fetch_path(client, learner_id) -> None:
    generated = client.post(f"/api/path/generate/{learner_id}")
    assert generated.status_code == 200, generated.text
    body = generated.json()
    assert body["version"] == 1
    assert body["items"], "a beginner's ML path must not be empty"
    assert body["finish_week"] == max(i["week_number"] for i in body["items"])

    fetched = client.get(f"/api/path/{learner_id}").json()
    assert fetched["path_id"] == body["path_id"]
    assert len(fetched["items"]) == len(body["items"])


def test_free_only_learner_gets_zero_paid_resources(client, learner_id) -> None:
    body = client.get(f"/api/path/{learner_id}").json()
    for item in body["items"]:
        if item["course"]:
            assert item["course"]["cost"] == "free"


def test_every_item_has_a_rationale_and_provenance(client, learner_id) -> None:
    body = client.get(f"/api/path/{learner_id}").json()
    for item in body["items"]:
        assert item["rationale_text"], f"{item['skill_id']} has no reason"
        assert item["provenance"]


def test_whatif_changes_nothing_in_the_database(client, learner_id) -> None:
    from app.db import engine
    from sqlmodel import Session, select
    from app.models import LearningPath, PathItem

    def fingerprint() -> tuple:
        with Session(engine) as db:
            paths = db.exec(select(LearningPath).where(
                LearningPath.learner_id == learner_id)).all()
            items = db.exec(select(PathItem)).all()
        return (
            tuple(sorted((p.id, p.version, p.status, p.finish_week) for p in paths)),
            tuple(sorted((i.id, i.week_number, i.course_id or "") for i in items)),
        )

    before = fingerprint()
    baseline = client.get(f"/api/path/{learner_id}").json()

    doubled = client.post(
        "/api/path/whatif", json={"learner_id": learner_id, "hours_per_week": 24}
    ).json()
    assert doubled["persisted"] is False
    assert doubled["finish_week"] < baseline["finish_week"]
    assert doubled["weeks"]

    assert fingerprint() == before, "whatif must leave the database untouched"


def test_whatif_rejects_a_nonsense_capacity(client, learner_id) -> None:
    assert client.post(
        "/api/path/whatif", json={"learner_id": learner_id, "hours_per_week": 0}
    ).status_code == 422


def test_unknown_learner_is_a_404_not_a_500(client) -> None:
    for url in ("/api/path/999999", "/api/dashboard/999999",
                "/api/graph/999999", "/api/diagnostic/next/999999"):
        assert client.get(url).status_code == 404, url


# --------------------------------------------------------------------------- #
# Adaptation (Phase 7)
# --------------------------------------------------------------------------- #
def test_too_easy_drops_the_skill_and_pulls_the_schedule_forward(client, learner_id) -> None:
    before = client.get(f"/api/path/{learner_id}").json()
    target = next(i for i in before["items"] if i["kind"] == "resource")

    response = client.post(
        "/api/path/event",
        json={"learner_id": learner_id, "type": "too_easy",
              "payload": {"skill_id": target["skill_id"]}},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["version"] == before["version"] + 1
    assert body["diff"]["unchanged"] is False
    assert any(r["skill_id"] == target["skill_id"] for r in body["diff"]["removed"])

    after = client.get(f"/api/path/{learner_id}").json()
    assert target["skill_id"] not in [i["skill_id"] for i in after["items"]]
    assert after["finish_week"] <= before["finish_week"]


def test_older_versions_stay_retrievable(client, learner_id) -> None:
    current = client.get(f"/api/path/{learner_id}").json()
    older = client.get(f"/api/path/{learner_id}?version=1").json()
    assert older["version"] == 1
    assert older["status"] == "superseded"
    assert current["version"] > 1


def test_diff_between_two_versions_is_exact(client, learner_id) -> None:
    current = client.get(f"/api/path/{learner_id}").json()
    diff = client.get(f"/api/path/{learner_id}/diff/1/{current['version']}").json()

    v1_skills = {
        (i["skill_id"], i["kind"])
        for i in client.get(f"/api/path/{learner_id}?version=1").json()["items"]
    }
    v2_skills = {(i["skill_id"], i["kind"]) for i in current["items"]}

    assert {(a["skill_id"], a["kind"]) for a in diff["added"]} == v2_skills - v1_skills
    assert {(r["skill_id"], r["kind"]) for r in diff["removed"]} == v1_skills - v2_skills


def test_diff_of_a_version_with_itself_is_empty(client, learner_id) -> None:
    current = client.get(f"/api/path/{learner_id}").json()["version"]
    diff = client.get(f"/api/path/{learner_id}/diff/{current}/{current}").json()
    assert diff["unchanged"] is True
    assert diff["added"] == diff["removed"] == diff["moved_weeks"] == []
    assert diff["finish_week_delta"] == 0


def test_diff_of_a_missing_version_is_a_404(client, learner_id) -> None:
    assert client.get(f"/api/path/{learner_id}/diff/1/999").status_code == 404


def test_milestone_failed_reinstates_remediation(client, learner_id) -> None:
    path = client.get(f"/api/path/{learner_id}").json()
    target = next(i for i in path["items"] if i["kind"] == "resource")

    client.post("/api/path/event", json={
        "learner_id": learner_id, "type": "completed_item",
        "payload": {"skill_id": target["skill_id"]}})

    response = client.post("/api/path/event", json={
        "learner_id": learner_id, "type": "milestone_failed",
        "payload": {"skill_id": target["skill_id"]}})
    assert response.status_code == 200

    after = client.get(f"/api/path/{learner_id}").json()
    assert target["skill_id"] in [i["skill_id"] for i in after["items"]], (
        "a failed checkpoint must bring the skill back"
    )


def test_resource_disliked_rebinds_to_the_next_best(client, learner_id) -> None:
    path = client.get(f"/api/path/{learner_id}").json()
    target = next(
        (i for i in path["items"] if i["course"] and i["alternatives"]), None
    )
    if target is None:
        pytest.skip("no item in this path has an alternative resource")

    response = client.post("/api/path/event", json={
        "learner_id": learner_id, "type": "resource_disliked",
        "payload": {"item_id": target["id"]}})
    assert response.status_code == 200

    after = client.get(f"/api/path/{learner_id}").json()
    updated = next(i for i in after["items"] if i["id"] == target["id"])
    assert updated["course"]["id"] != target["course"]["id"]
    assert updated["course"]["id"] == target["alternatives"][0]["id"]


def test_behind_schedule_returns_scope_options(client, learner_id) -> None:
    response = client.post("/api/path/event", json={
        "learner_id": learner_id, "type": "behind_schedule",
        "payload": {"weeks_behind": 3}})
    assert response.status_code == 200
    assert len(response.json()["options"]) >= 3


def test_goal_changed_preserves_overlapping_progress(client, learner_id) -> None:
    from app.db import engine
    from sqlmodel import Session, select
    from app.models import Mastery

    with Session(engine) as db:
        before = {
            row.skill_id: row.score
            for row in db.exec(select(Mastery).where(Mastery.learner_id == learner_id)).all()
            if row.score >= 0.7
        }

    response = client.post("/api/path/event", json={
        "learner_id": learner_id, "type": "goal_changed",
        "payload": {"goal_node_ids": ["da.analyst"]}})
    assert response.status_code == 200, response.text
    assert response.json()["diff"]["unchanged"] is False

    with Session(engine) as db:
        after = {
            row.skill_id: row.score
            for row in db.exec(select(Mastery).where(Mastery.learner_id == learner_id)).all()
            if row.score >= 0.7
        }
    for skill_id, score in before.items():
        assert after.get(skill_id) == score, f"{skill_id} lost its measured mastery"

    path = client.get(f"/api/path/{learner_id}").json()
    assert path["goal_node_ids"] == ["da.analyst"]
    for skill_id in before:
        assert skill_id not in [i["skill_id"] for i in path["items"]], (
            "already-mastered overlapping skills must not be re-taught"
        )


def test_an_event_before_any_path_exists_is_a_409(client) -> None:
    fresh = client.post("/api/intake/commit", json={
        "profile": {"goal_text": "learn cybersecurity", "hours_per_week": 4}}).json()["learner_id"]
    response = client.post("/api/path/event", json={
        "learner_id": fresh, "type": "too_easy", "payload": {"skill_id": "prog.cli"}})
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# Dashboard, graph and chat
# --------------------------------------------------------------------------- #
def test_dashboard_reports_consistent_totals(client, learner_id) -> None:
    body = client.get(f"/api/dashboard/{learner_id}").json()
    path = client.get(f"/api/path/{learner_id}").json()

    assert body["items_total"] == len(path["items"])
    assert body["finish_week"] == path["finish_week"]
    assert 0 <= body["progress_pct"] <= 100
    assert len(body["next_actions"]) <= 3
    assert body["mastery_radar"]
    assert body["activity"], "the event log drives the feed"


def test_graph_is_annotated_and_bounded(client, learner_id) -> None:
    body = client.get(f"/api/graph/{learner_id}").json()
    node_ids = {n["id"] for n in body["nodes"]}

    assert body["nodes"] and body["edges"]
    assert any(n["is_goal"] for n in body["nodes"])
    assert any(n["in_path"] for n in body["nodes"])
    for edge in body["edges"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids
    for node in body["nodes"]:
        assert 0.0 <= node["mastery"] <= 1.0


def test_chat_is_grounded_in_this_learners_path(client, learner_id) -> None:
    body = client.post(f"/api/chat/{learner_id}", json={"message": "what should I do first?"}).json()
    assert body["reply"]
    path = client.get(f"/api/path/{learner_id}").json()
    bound = {i["course"]["url"] for i in path["items"] if i["course"]}
    for citation in body["citations"]:
        assert citation["url"] in bound, "a citation must point at this learner's own resource"


def test_chat_rejects_an_empty_message(client, learner_id) -> None:
    assert client.post(f"/api/chat/{learner_id}", json={"message": ""}).status_code == 422
