"""Phase 0 acceptance: the process boots, and /health is fast and honest."""

from __future__ import annotations

import time


def test_health_returns_ok(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body) >= {
        "version",
        "llm_available",
        "llm_provider",
        "catalog_size",
        "graph_nodes",
        "graph_tracks",
    }
    assert isinstance(body["catalog_size"], int)
    assert isinstance(body["graph_nodes"], int)


def test_health_is_fast_and_makes_no_model_call(client, monkeypatch) -> None:
    """Under 50ms, and any attempt to reach the provider fails the test."""
    from app.llm.mock import MockProvider

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("/health must not call the LLM")

    monkeypatch.setattr(MockProvider, "complete", _forbidden)
    monkeypatch.setattr(MockProvider, "embed", _forbidden)

    client.get("/health")  # warm any lazy import
    durations = []
    for _ in range(20):
        start = time.perf_counter()
        assert client.get("/health").status_code == 200
        durations.append(time.perf_counter() - start)

    median = sorted(durations)[len(durations) // 2]
    assert median < 0.050, f"median /health latency {median * 1000:.1f}ms exceeds 50ms"


def test_openapi_schema_builds(client) -> None:
    assert client.get("/openapi.json").status_code == 200
