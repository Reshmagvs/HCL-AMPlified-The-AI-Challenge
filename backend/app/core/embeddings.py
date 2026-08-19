"""Local sentence embeddings -- no API key, no quota, no network at request time.

Embeddings used to come from the same provider that generated text, which tied
retrieval quality to an API key and meant a rate limit could degrade *search*
rather than just wording. It also forced one committed matrix per provider,
because vectors from two different models cannot be compared.

Both problems disappear if embedding is a local, deterministic function. It is
not a "language model" concern at all -- it is a similarity function over a fixed
vocabulary of skills and resources, and it belongs in `core/` with the rest of
the deterministic layer.

Two implementations, chosen automatically:

``FastEmbedder`` -- BAAI/bge-small-en-v1.5 through ONNX Runtime. 384 dimensions,
a ~130 MB model cached on first use, and roughly 13 ms per text on a four-core
laptop CPU. Only the learner's goal text is ever embedded at request time, so
this never appears in the critical path more than once per request.

``HashingEmbedder`` -- a signed hashing vectoriser over word tokens and character
trigrams. No model, no download, works offline forever. Cosine similarity tracks
lexical overlap rather than meaning, which is materially worse but never wrong in
an alarming way. It exists so a clone with no internet still resolves goals
sensibly instead of comparing noise.

The active embedder names the matrix files it produces, so a mismatch is
impossible: `retrieval` loads `catalog_embeddings.<name>.npy` and refuses a
matrix whose row count or dimension disagrees with the data.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from functools import lru_cache
from typing import Protocol

import numpy as np

logger = logging.getLogger(__name__)

FAST_MODEL = "BAAI/bge-small-en-v1.5"
HASHING_DIM = 512
_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")


class Embedder(Protocol):
    """Anything that turns text into comparable unit vectors."""

    name: str
    dim: int

    def embed_batch(self, texts: list[str]) -> np.ndarray: ...


def _l2(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (matrix / norms).astype(np.float32)


class FastEmbedder:
    """Transformer embeddings via ONNX Runtime. CPU-only, no API."""

    name = "bge-small"
    dim = 384

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=FAST_MODEL)

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _l2(np.asarray(list(self._model.embed(texts)), dtype=np.float32))


class HashingEmbedder:
    """Offline fallback: signed hashing over words plus character trigrams.

    Word tokens are weighted above trigrams so an exact term match dominates a
    coincidental substring match. Deterministic by construction -- the same text
    always produces the same vector, which is what the determinism tests rely on.
    """

    name = "hashing"
    dim = HASHING_DIM

    @staticmethod
    def _digest(token: str) -> int:
        return int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:8], "big")

    def embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        words = _TOKEN_RE.findall(text.lower())
        grams = [w[i : i + 3] for w in words if len(w) > 3 for i in range(len(w) - 2)]

        for token in [*words, *grams]:
            digest = self._digest(token)
            sign = 1.0 if (digest >> 61) & 1 else -1.0
            vector[digest % self.dim] += sign * (2.0 if token in words else 0.6)

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.asarray([self.embed_one(t) for t in texts], dtype=np.float32)


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """The best embedder this machine can actually load, decided once."""
    from app.config import get_settings

    if get_settings().embedder != "hashing":
        try:
            embedder = FastEmbedder()
            logger.info("embeddings: %s (local, %d dims)", FAST_MODEL, embedder.dim)
            return embedder
        except Exception as exc:  # noqa: BLE001 -- missing package or model download
            logger.warning(
                "local embedding model unavailable (%s) -- falling back to the "
                "offline hashing vectoriser. Run `pip install fastembed` and "
                "`python -m scripts.build_embeddings` for better retrieval.",
                str(exc)[:160],
            )
    return HashingEmbedder()


def reset_embedder() -> None:
    """Drop the cached embedder. Used by scripts and tests that switch modes."""
    get_embedder.cache_clear()


def embed_one(text: str) -> np.ndarray:
    """Embed a single string. The only embedding call on a request path."""
    return get_embedder().embed_batch([text])[0]
