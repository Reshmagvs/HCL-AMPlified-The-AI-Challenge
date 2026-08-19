"""Google Gemini provider.

Three concerns beyond "call the API":

* **One retry, then give up loudly.** Transient 429/5xx get a single backoff
  retry. Anything past that raises ``ProviderUnavailable``, which every caller
  already handles by degrading. Retrying harder would turn a rate limit into a
  slow request, and a slow request into a failed demo.
* **No silent success.** A blank completion is treated as a failure, not as an
  empty answer, so schema validation never receives an empty string it would
  reject with a confusing message.
"""

from __future__ import annotations

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
                self._broken = False  # a success clears an earlier transient failure
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
