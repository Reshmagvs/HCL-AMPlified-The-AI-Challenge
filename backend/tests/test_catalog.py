"""Phase 2 acceptance: the catalog is well-formed, grounded and filterable.

The point of the catalog is that a recommendation can never be invented. These
tests check the structural half of that claim -- shape, coverage, and that hard
filters really are hard. The other half (every URL returns 2xx) is enforced by
`scripts/verify_catalog.py`, which is what produced the file, and re-run as a
live link check in the final system pass.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from app.core.retrieval import (
    Preferences,
    catalog_index,
    load_matrices,
    passes_hard_filters,
    score_resources,
)

VALID_FORMATS = {"video", "text", "interactive", "course"}
VALID_COSTS = {"free", "paid"}
VALID_LEVELS = {"beginner", "intermediate", "advanced"}


def test_catalog_is_large_enough_to_offer_choice(catalog) -> None:
    assert len(catalog) >= 400, f"only {len(catalog)} verified resources"


def test_every_entry_is_well_formed(catalog, graph) -> None:
    for resource in catalog:
        assert resource.url.startswith("https://"), resource.url
        assert resource.format in VALID_FORMATS, resource.format
        assert resource.cost in VALID_COSTS, resource.cost
        assert resource.level in VALID_LEVELS, resource.level
        assert resource.duration_hours > 0, resource.id
        assert resource.title.strip()
        assert resource.provider.strip()
        assert 3.0 <= resource.rating <= 5.0
        resolvable = [s for s in resource.skills_covered if s in graph]
        assert resolvable, f"{resource.id} covers no skill that exists"


def test_ids_are_unique(catalog) -> None:
    ids = [r.id for r in catalog]
    assert len(ids) == len(set(ids))


def test_urls_are_unique(catalog) -> None:
    urls = [r.url.rstrip("/").lower() for r in catalog]
    duplicates = {u for u in urls if urls.count(u) > 1}
    assert not duplicates, f"duplicate urls survived verification: {list(duplicates)[:5]}"


def test_at_least_sixty_percent_free(catalog) -> None:
    free = sum(1 for r in catalog if r.cost == "free")
    ratio = free / len(catalog)
    assert ratio >= 0.60, f"only {ratio:.1%} free"


def test_every_assessable_node_has_a_resource(graph, catalog) -> None:
    covered = {skill for resource in catalog for skill in resource.skills_covered}
    missing = sorted(n.id for n in graph.nodes.values() if n.assessable and n.id not in covered)
    assert missing == [], f"{len(missing)} assessable nodes have no resource: {missing[:10]}"


def test_every_track_is_represented(graph, catalog) -> None:
    tracks = {
        graph.require(skill).track
        for resource in catalog
        for skill in resource.skills_covered
        if skill in graph
    }
    assert set(graph.tracks) <= tracks


# --------------------------------------------------------------------------- #
# Embeddings
# --------------------------------------------------------------------------- #
def test_embedding_matrices_match_the_data_and_contain_no_nan(catalog, graph) -> None:
    matrices = load_matrices()
    assert matrices["catalog"] is not None, "catalog embeddings are missing"
    assert matrices["skills"] is not None, "skill embeddings are missing"

    assert matrices["catalog"].shape[0] == len(catalog)
    assert matrices["skills"].shape[0] == len(graph)
    assert matrices["catalog"].shape[1] == matrices["skills"].shape[1]

    for name in ("catalog", "skills"):
        matrix = matrices[name]
        assert np.isfinite(matrix).all(), f"{name} embeddings contain NaN or inf"
        norms = np.linalg.norm(matrix, axis=1)
        assert np.abs(norms - 1.0).max() < 1e-4, f"{name} embeddings are not L2-normalised"


def test_matrices_exist_for_the_active_provider() -> None:
    """A clone with no API key must not fall back to comparing noise."""
    from app.core.retrieval import matrix_path

    for provider in ("gemini", "mock"):
        for kind in ("catalog", "skill"):
            assert matrix_path(kind, provider).exists(), f"{kind}/{provider} matrix is missing"


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def test_free_only_returns_zero_paid_across_two_hundred_results(graph) -> None:
    prefs = Preferences(cost_pref="free")
    rng = random.Random(7)
    skills = rng.sample(sorted(graph.nodes), 120)
    scored = 0

    for skill_id in skills:
        for result in score_resources(skill_id, graph.require(skill_id).difficulty, prefs, top_k=3):
            assert result.resource.cost == "free"
            scored += 1
    assert scored >= 200, f"only {scored} scored results -- the assertion is too weak"


def test_hard_filters_run_before_scoring(catalog) -> None:
    """A high-scoring paid resource must not survive a free-only request."""
    paid = next((r for r in catalog if r.cost == "paid"), None)
    if paid is None:
        pytest.skip("the verified catalog happens to contain no paid resources")
    assert passes_hard_filters(paid, Preferences(cost_pref="free")) is False
    assert passes_hard_filters(paid, Preferences(cost_pref="any")) is True


def test_low_bandwidth_removes_video(catalog) -> None:
    video = next((r for r in catalog if r.format == "video"), None)
    assert video is not None, "the catalog needs some video for this filter to mean anything"
    assert passes_hard_filters(video, Preferences(low_bandwidth=True)) is False


def test_language_filter_is_applied(catalog) -> None:
    resource = catalog[0]
    assert passes_hard_filters(resource, Preferences(language="fr")) is False


def test_scoring_is_deterministic(graph) -> None:
    prefs = Preferences(format_pref="video", cost_pref="free")
    first = [
        (s.resource.id, s.score)
        for s in score_resources("ml.gradient_descent", 3, prefs)
    ]
    for _ in range(5):
        assert [
            (s.resource.id, s.score) for s in score_resources("ml.gradient_descent", 3, prefs)
        ] == first


def test_ranking_returns_at_most_three_with_the_best_first(graph) -> None:
    results = score_resources("web.react", 3, Preferences())
    assert len(results) <= 3
    assert results == sorted(results, key=lambda s: (-s.score, s.resource.id))


def test_format_preference_shifts_the_ranking(graph, catalog) -> None:
    """The preference has to actually change something, or it is decoration."""
    changed = 0
    for skill_id in sorted(graph.nodes):
        video = score_resources(skill_id, 3, Preferences(format_pref="video"))
        text = score_resources(skill_id, 3, Preferences(format_pref="text"))
        if video and text and video[0].resource.id != text[0].resource.id:
            changed += 1
    assert changed > 0, "format preference never altered a top pick"
