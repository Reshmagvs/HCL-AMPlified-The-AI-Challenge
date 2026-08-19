"""Language layer: the only part of the system allowed to call a model."""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.llm.base import LLMProvider, ProviderUnavailable, SchemaViolation

__all__ = ["LLMProvider", "ProviderUnavailable", "SchemaViolation", "get_provider", "reset_provider"]


@lru_cache(maxsize=1)
def get_provider() -> LLMProvider:
    """Return the configured provider, constructed once per process."""
    settings = get_settings()
    if settings.llm_provider.lower() == "gemini" and settings.gemini_api_key:
        from app.llm.gemini import GeminiProvider

        return GeminiProvider()
    from app.llm.mock import MockProvider

    return MockProvider()


def reset_provider() -> None:
    """Drop the cached provider. Used by tests that flip configuration."""
    get_provider.cache_clear()
