"""Embeddings are local, deterministic and provider-independent.

These properties are what let retrieval work with no API key: a rate limit can
degrade *wording*, but it must never degrade *search*.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.embeddings import HashingEmbedder, get_embedder


@pytest.fixture(scope="module")
def embedder():
    return get_embedder()


def _cosine(embedder, a: str, b: str) -> float:
    vectors = embedder.embed_batch([a, b])
    return float(vectors[0] @ vectors[1])


def test_vectors_are_unit_length(embedder) -> None:
    matrix = embedder.embed_batch(["gradient descent", "responsive css layout", ""])
    norms = np.linalg.norm(matrix, axis=1)
    assert np.abs(norms - 1.0).max() < 1e-4


def test_embedding_is_deterministic(embedder) -> None:
    first = embedder.embed_batch(["gradient descent and the learning rate"])
    second = embedder.embed_batch(["gradient descent and the learning rate"])
    assert np.array_equal(first, second)


def test_empty_text_does_not_divide_by_zero(embedder) -> None:
    vector = embedder.embed_batch([""])[0]
    assert abs(float(np.linalg.norm(vector)) - 1.0) < 1e-4


def test_similarity_tracks_meaning(embedder) -> None:
    """Related text must score above unrelated text, whichever model is active."""
    related = _cosine(embedder, "machine learning engineer", "neural networks and training")
    unrelated = _cosine(embedder, "machine learning engineer", "responsive css media queries")
    assert related > unrelated


def test_hashing_fallback_is_usable_on_its_own() -> None:
    """The offline path has to work, because it is what a no-internet clone gets."""
    fallback = HashingEmbedder()
    related = _cosine(fallback, "python functions and scope", "python function arguments")
    unrelated = _cosine(fallback, "python functions and scope", "kubernetes ingress routing")

    assert related > unrelated
    assert fallback.dim == 512
    assert np.array_equal(fallback.embed_batch(["git"]), fallback.embed_batch(["git"]))


def test_embedder_names_its_matrices(embedder) -> None:
    """A matrix file must be attributable to the model that produced it."""
    from app.core.retrieval import matrix_path

    assert embedder.name in matrix_path("catalog", embedder.name).name
    assert matrix_path("catalog", "a") != matrix_path("catalog", "b")


def test_no_llm_provider_exposes_embeddings() -> None:
    """Embedding is not a language-model concern and must not creep back in."""
    from app.llm.base import LLMProvider
    from app.llm.mock import MockProvider

    assert not hasattr(LLMProvider, "embed")
    assert not hasattr(MockProvider(), "embed")
