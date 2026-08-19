"""Google Gemini provider.

Three concerns beyond "call the API":

* **Caching.** Embeddings are keyed by ``sha256(model + text)`` and stored in
  the ``EmbeddingCache`` table plus an in-process dict. Catalog and skill
  vectors are precomputed into ``.npy`` files, so at request time only the
  learner's goal text is ever new -- which is what keeps a fresh clone at
  essentially zero API cost.
* **One retry, then give up loudly.** Transient 429/5xx get a single backoff
  retry. Anything past that raises ``ProviderUnavailable``, which every caller
  already handles by degrading. Retrying harder would turn a rate limit into a
  slow request, and a slow request into a failed demo.
* **No silent success.** A blank completion is treated as a failure, not as an
  empty answer, so schema validation never receives an empty string it would
  reject with a confusing message.
"""

from __future__ import annotations

import hashlib
import logging
import time

from app.config import get_settings
from app.llm.base import LLMProvider, ProviderUnavailable

logger = logging.getLogger(__name__)

_RETRYABLE = ("429", "500", "502", "503", "504", "deadline", "timeout", "unavailable")


def _is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in _RETRYABLE)


class GeminiProvider(LLMProvider):
    """Generation via ``gemini-2.0-flash``, embeddings via ``text-embedding-004``."""

    name = "gemini"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._memo: dict[str, list[float]] = {}
        self._client = None
        self._broken = False

    # -- plumbing -----------------------------------------------------------
    @property
    def client(self):
        """Lazily construct the SDK client so importing this module is free."""
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    def available(self) -> bool:
        """True when a key is configured and no call has hard-failed yet."""
        return bool(self.settings.gemini_api_key) and not self._broken

    def _guard(self) -> None:
        if not self.settings.gemini_api_key:
            raise ProviderUnavailable("GEMINI_API_KEY is not set")

    # -- generation ---------------------------------------------------------
    def complete(self, prompt: str, *, temperature: float = 0.2, max_tokens: int = 2048) -> str:
        """One call, one retry on transient failure, then ``ProviderUnavailable``."""
        self._guard()
        config = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
        }
        for attempt in (1, 2):
            try:
                response = self.client.models.generate_content(
                    model=self.settings.gemini_model, contents=prompt, config=config
                )
                text = (response.text or "").strip()
                if not text:
                    raise ProviderUnavailable("gemini returned an empty completion")
                return text
            except Exception as exc:  # noqa: BLE001 -- SDK raises a wide family
                if attempt == 1 and _is_retryable(exc):
                    logger.warning("gemini transient failure, retrying: %s", exc)
                    time.sleep(1.5)
                    continue
                self._broken = True
                logger.error("gemini call failed: %s", exc)
                raise ProviderUnavailable(str(exc)) from exc
        raise ProviderUnavailable("gemini exhausted retries")

    # -- embeddings ---------------------------------------------------------
    def _cache_key(self, text: str) -> str:
        return hashlib.sha256(f"{self.settings.gemini_embed_model}\x00{text}".encode()).hexdigest()

    def embed(self, text: str) -> list[float]:
        """Return a cached vector when possible; otherwise embed and persist it."""
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed only the texts missing from cache, then reassemble in order."""
        self._guard()
        keys = [self._cache_key(t) for t in texts]
        results: dict[str, list[float]] = {k: self._memo[k] for k in keys if k in self._memo}
        self._load_cached(keys, results)

        pending = [(k, t) for k, t in zip(keys, texts, strict=True) if k not in results]
        if pending:
            vectors = self._embed_remote([t for _, t in pending])
            for (key, text), vector in zip(pending, vectors, strict=True):
                results[key] = vector
                self._memo[key] = vector
                self._persist(key, text, vector)
        return [results[k] for k in keys]

    def _embed_remote(self, texts: list[str]) -> list[list[float]]:
        for attempt in (1, 2):
            try:
                response = self.client.models.embed_content(
                    model=self.settings.gemini_embed_model, contents=texts
                )
                return [list(e.values) for e in response.embeddings]
            except Exception as exc:  # noqa: BLE001
                if attempt == 1 and _is_retryable(exc):
                    logger.warning("gemini embed transient failure, retrying: %s", exc)
                    time.sleep(1.5)
                    continue
                self._broken = True
                raise ProviderUnavailable(str(exc)) from exc
        raise ProviderUnavailable("gemini embed exhausted retries")

    # -- cache persistence --------------------------------------------------
    def _load_cached(self, keys: list[str], into: dict[str, list[float]]) -> None:
        missing = [k for k in keys if k not in into]
        if not missing:
            return
        try:
            from sqlmodel import Session, select

            from app.db import engine
            from app.models import EmbeddingCache

            with Session(engine) as session:
                rows = session.exec(
                    select(EmbeddingCache).where(EmbeddingCache.text_hash.in_(missing))
                ).all()
            for row in rows:
                into[row.text_hash] = row.vector
                self._memo[row.text_hash] = row.vector
        except Exception as exc:  # noqa: BLE001 -- cache is an optimisation, never fatal
            logger.debug("embedding cache read skipped: %s", exc)

    def _persist(self, key: str, _text: str, vector: list[float]) -> None:
        try:
            from sqlmodel import Session

            from app.db import engine
            from app.models import EmbeddingCache

            with Session(engine) as session:
                session.merge(
                    EmbeddingCache(
                        text_hash=key, model=self.settings.gemini_embed_model, vector=vector
                    )
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("embedding cache write skipped: %s", exc)
