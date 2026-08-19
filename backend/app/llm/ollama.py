"""Local text generation through Ollama.

An API key is a dependency on someone else's rate limit, quota and uptime. This
provider removes that: Ollama runs a small model on the same machine, so the
language layer costs nothing, never rate-limits, and works offline.

The trade-off is honest. A 1B model on a four-core laptop CPU produces perhaps
ten tokens a second, which is fine for the two things generation is actually
used for at request time -- an intake reply and a chat answer -- and far too slow
to narrate forty path items one by one. So narration falls back to templates
whenever a call would be slow enough to notice, and the product is designed so
that costs nothing: the reason text is computed either way.

Availability is checked once against ``/api/tags`` with a short timeout and then
cached, so a machine without Ollama pays a few hundred milliseconds at startup
and nothing afterwards.

    ollama pull llama3.2:1b      # ~1.3 GB, the default
    ollama pull qwen2.5:3b       # better prose if you have the RAM
"""

from __future__ import annotations

import logging
import time

import httpx

from app.config import get_settings
from app.llm.base import LLMProvider, ProviderUnavailable

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 2.0
GENERATE_TIMEOUT = 180.0


class OllamaProvider(LLMProvider):
    """Generation via a locally running Ollama daemon."""

    name = "ollama"

    def __init__(self) -> None:
        settings = get_settings()
        self.host = settings.ollama_host.rstrip("/")
        self.model = settings.ollama_model
        self._available: bool | None = None

    # -- capability ---------------------------------------------------------
    def available(self) -> bool:
        """True when the daemon answers and has the configured model pulled."""
        if self._available is not None:
            return self._available
        self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        try:
            response = httpx.get(f"{self.host}/api/tags", timeout=PROBE_TIMEOUT)
            response.raise_for_status()
            names = {m.get("name", "") for m in response.json().get("models", [])}
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("ollama not reachable at %s (%s)", self.host, str(exc)[:100])
            return False

        # Ollama reports "llama3.2:1b"; accept a bare family name as a match too.
        if self.model in names or any(n.split(":")[0] == self.model for n in names):
            logger.info("ollama ready at %s with %s", self.host, self.model)
            return True
        logger.warning(
            "ollama is running but %r is not pulled (have: %s). Run: ollama pull %s",
            self.model, ", ".join(sorted(names)) or "nothing", self.model,
        )
        return False

    def refresh(self) -> None:
        """Re-probe. Used after a user starts Ollama without restarting the API."""
        self._available = None

    # -- generation ---------------------------------------------------------
    def complete(self, prompt: str, *, temperature: float = 0.2, max_tokens: int = 2048) -> str:
        """One blocking generation. Raises ``ProviderUnavailable`` on any failure."""
        if not self.available():
            raise ProviderUnavailable(f"ollama has no {self.model} at {self.host}")

        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{self.host}/api/generate",
                timeout=GENERATE_TIMEOUT,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                        # A small context keeps a CPU-only model responsive; the
                        # prompts here are short by design.
                        "num_ctx": 4096,
                    },
                },
            )
            response.raise_for_status()
            text = (response.json().get("response") or "").strip()
        except (httpx.HTTPError, ValueError) as exc:
            self._available = None  # the daemon may have stopped; re-probe next time
            raise ProviderUnavailable(f"ollama call failed: {str(exc)[:160]}") from exc

        elapsed = time.perf_counter() - started
        if not text:
            raise ProviderUnavailable("ollama returned an empty completion")
        logger.debug("ollama generated %d chars in %.1fs", len(text), elapsed)
        return text
