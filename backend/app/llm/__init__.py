"""Language layer: the only part of the system allowed to call a model.

Four providers, chosen by ``LLM_PROVIDER``:

``mock``        deterministic, offline, always available. What the test suite runs on.
``ollama``      a small model on this machine. No key, no quota, no network.
``openrouter``  a hosted model, over free models only. Fast, and rate-limited.
``gemini``      a hosted model, when a key is present. Kept, not used by default.

``auto`` builds a *chain* rather than picking one, and the order is the point.

OpenRouter goes first because latency is what makes a conversation possible: the
same extraction takes 3.5 s there and 43 s on the local model, and the latency
budgets in ``app.config`` correctly refuse to put 43 s in front of someone
waiting -- which is why intake used to answer from templates and repeat itself.

Ollama goes second because it is the thing that cannot run out. Free hosted
models are throttled without warning, and when they are, a local model that
takes a minute is worth far more than an error. It is also what keeps the
promise that this product works with no account and no network.

Mock goes last so that even with nothing installed, every screen still returns
a real answer with templated wording.

Nothing outside this package knows which one is active. Retrieval does not
appear in this list because embeddings are local and deterministic regardless
of provider -- see ``core.embeddings``.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import get_settings
from app.llm.base import LLMProvider, ProviderUnavailable, SchemaViolation

logger = logging.getLogger(__name__)

__all__ = [
    "ChainProvider",
    "LLMProvider",
    "ProviderUnavailable",
    "SchemaViolation",
    "get_provider",
    "reset_provider",
]


class ChainProvider(LLMProvider):
    """Tries each provider in turn, falling through on unavailability.

    The failure this exists to prevent is a whole screen degrading to templates
    because one hosted model was busy. A free model is throttled unpredictably;
    a local model is slow but never throttled; between them there is always an
    answer, and the caller does not need to know which one produced it.

    ``tokens_per_second`` reports the provider that would actually serve the
    next call, so the latency budgets stay honest: with a hosted model in front
    they permit the conversational work they refuse on a laptop CPU alone.
    """

    name = "chain"

    def __init__(self, providers: list[LLMProvider]) -> None:
        self.providers = providers

    @property
    def active(self) -> LLMProvider | None:
        """The first provider that would take a call right now."""
        return next((p for p in self.providers if p.available()), None)

    def available(self) -> bool:
        return self.active is not None

    def tokens_per_second(self) -> float:
        provider = self.active
        return provider.tokens_per_second() if provider else 1.0

    def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_schema: dict | None = None,
    ) -> str:
        errors: list[str] = []
        for provider in self.providers:
            if not provider.available():
                continue
            try:
                return provider.complete(
                    prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_schema=json_schema,
                )
            except ProviderUnavailable as exc:
                errors.append(f"{provider.name}: {str(exc)[:120]}")
                logger.info("provider %s unavailable, trying the next", provider.name)
        raise ProviderUnavailable("; ".join(errors) or "no provider is configured")

    def describe(self) -> str:
        """Which provider is in front, for /health and the usage panel."""
        provider = self.active
        return provider.name if provider else "none"


def _openrouter():
    from app.llm.openrouter import OpenRouterProvider

    return OpenRouterProvider()


def _ollama():
    from app.llm.ollama import OllamaProvider

    return OllamaProvider()


def _mock():
    from app.llm.mock import MockProvider

    return MockProvider()


def _build(name: str) -> LLMProvider:
    settings = get_settings()

    if name == "gemini" and settings.gemini_api_key:
        from app.llm.gemini import GeminiProvider

        return GeminiProvider()

    if name == "openrouter":
        return _openrouter()

    if name == "ollama":
        return _ollama()

    if name in ("auto", ""):
        chain: list[LLMProvider] = []
        if settings.openrouter_api_key:
            chain.append(_openrouter())
        ollama = _ollama()
        if ollama.available():
            chain.append(ollama)
        chain.append(_mock())
        if len(chain) == 1:
            logger.info("no hosted key and no local daemon -- using the offline provider")
            return chain[0]
        logger.info("provider chain: %s", " -> ".join(p.name for p in chain))
        return ChainProvider(chain)

    return _mock()


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
