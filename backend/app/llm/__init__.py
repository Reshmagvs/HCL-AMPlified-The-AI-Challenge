"""Language layer: the only part of the system allowed to call a model.

Three providers, chosen by ``LLM_PROVIDER``:

``mock``    deterministic, offline, always available. The default, and the
            configuration the entire test suite runs under.
``ollama``  a small model on this machine. No key, no quota, no network.
``gemini``  a hosted model, when a key is present.

``auto`` picks Ollama if its daemon is answering and falls back to mock, which
is the right default for a machine that may or may not have it installed.

Nothing outside this package knows which one is active, and nothing in ``core/``
imports from here at all. Retrieval does not appear in this list because
embeddings are local and deterministic regardless of provider -- see
``core.embeddings``.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import get_settings
from app.llm.base import LLMProvider, ProviderUnavailable, SchemaViolation

logger = logging.getLogger(__name__)

__all__ = [
    "LLMProvider",
    "ProviderUnavailable",
    "SchemaViolation",
    "get_provider",
    "reset_provider",
]


def _build(name: str) -> LLMProvider:
    settings = get_settings()

    if name == "gemini" and settings.gemini_api_key:
        from app.llm.gemini import GeminiProvider

        return GeminiProvider()

    if name in ("ollama", "auto"):
        from app.llm.ollama import OllamaProvider

        provider = OllamaProvider()
        if name == "ollama" or provider.available():
            return provider
        logger.info("no local Ollama daemon -- using the offline provider")

    from app.llm.mock import MockProvider

    return MockProvider()


@lru_cache(maxsize=1)
def get_provider() -> LLMProvider:
    """Return the configured provider, constructed once per process."""
    requested = get_settings().llm_provider.lower().strip()
    provider = _build(requested)
    if provider.name != requested and requested not in ("auto", ""):
        logger.warning("LLM_PROVIDER=%s is unavailable; using %s", requested, provider.name)
    return provider


def reset_provider() -> None:
    """Drop the cached provider. Used by tests and scripts that flip configuration."""
    get_provider.cache_clear()
