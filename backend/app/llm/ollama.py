"""Local text generation through Ollama.

An API key is a dependency on someone else's rate limit, quota and uptime. This
provider removes that: a small model runs on the same machine, so the language
layer costs nothing, never rate-limits and works offline.

Three settings here came out of measurement on the development machine (a
four-core Ryzen 7 3700U with no usable GPU), because guessing them produced a
product that technically worked and was unusable:

**The model stays resident.** A cold load of qwen2.5:3b took 68 seconds, and
Ollama unloads after five minutes of idle by default. The first syllabus of the
day therefore spent more time loading the model than running it. ``keep_alive``
holds it in memory, and ``warm()`` pays the load during startup where a progress
line already explains the wait.

**Threads are pinned to physical cores.** Left to itself the runtime picked the
logical count and lost throughput to hyperthread contention.

**Decoding is constrained by schema, not by instruction.** Asked in words for a
syllabus, this model returned ``{"topic": "quantum-computing", "skills": []}``
-- valid JSON, 23 tokens, no content. Given the same request as a JSON schema
with ``minItems``, it produced thirteen skills with a sensible prerequisite
structure. That single change is the difference between the local model being a
toy and being the thing the product runs on.

Measured throughput, for anyone choosing a model. The 3B figure is a range
because it is a range: the same model on the same laptop measured 11 tok/s on
an otherwise idle machine and 3.8 tok/s with the API, the dev server and a
browser running. Nothing here assumes a number -- ``tokens_per_second`` is
updated from the last real generation and callers budget against that.

    qwen2.5:0.5b                    ~26 tok/s   too weak for structure
    qwen2.5:3b-instruct           3.8-11 tok/s  the default
    qwen2.5:7b-instruct-q4_K_M       ~5 tok/s   better answers, 119 s to load

    ollama pull qwen2.5:3b-instruct
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from app.config import get_settings
from app.llm.base import LLMProvider, ProviderUnavailable

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 2.0
GENERATE_TIMEOUT = 600.0
WARM_TIMEOUT = 300.0

# Long enough that a learner exploring the product never pays a reload, short
# enough that an idle machine gets its couple of gigabytes back eventually.
KEEP_ALIVE = "30m"


def _default_threads() -> int:
    """How many threads to decode with, when the setting does not say.

    Measured on this machine (4 physical cores, 8 logical), same model, same
    prompt, 171 generated tokens:

        num_thread=4   60.4 s   2.8 tok/s
        num_thread=8   45.6 s   3.8 tok/s

    An earlier version halved the logical count on the theory that
    hyperthread contention would cost more than the extra threads earned. On
    this hardware it does not, by 36%, so the count is measured rather than
    assumed. ``OLLAMA_THREADS`` overrides it for a machine where the older
    reasoning holds.
    """
    try:
        return max(1, os.cpu_count() or 4)
    except NotImplementedError:  # pragma: no cover - platform dependent
        return 4


class OllamaProvider(LLMProvider):
    """Generation via a locally running Ollama daemon."""

    name = "ollama"

    def __init__(self) -> None:
        settings = get_settings()
        self.host = settings.ollama_host.rstrip("/")
        self.model = settings.ollama_model
        self.num_ctx = settings.ollama_num_ctx
        self.threads = settings.ollama_threads or _default_threads()
        self._available: bool | None = None
        # Conservative until a real call measures it: a small model on a CPU
        # is single-digit tokens a second, and assuming otherwise would let
        # a caller commit to work that takes minutes.
        self._tokens_per_second = 5.0

    # -- capability ---------------------------------------------------------
    def available(self) -> bool:
        """True when the daemon answers and has the configured model pulled."""
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        try:
            response = httpx.get(f"{self.host}/api/tags", timeout=PROBE_TIMEOUT)
            response.raise_for_status()
            names = {model.get("name", "") for model in response.json().get("models", [])}
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("ollama not reachable at %s (%s)", self.host, str(exc)[:100])
            return False

        # Ollama reports "qwen2.5:3b-instruct"; accept a bare family name too.
        if self.model in names or any(n.split(":")[0] == self.model for n in names):
            logger.info("ollama ready at %s with %s", self.host, self.model)
            return True
        logger.warning(
            "ollama is running but %r is not pulled (have: %s). Run: ollama pull %s",
            self.model, ", ".join(sorted(names)) or "nothing", self.model,
        )
        return False

    def tokens_per_second(self) -> float:
        """Throughput from the most recent generation on this machine."""
        return self._tokens_per_second

    def refresh(self) -> None:
        """Re-probe. Used after Ollama is started without restarting the API."""
        self._available = None

    def warm(self) -> float:
        """Load the model into memory now. Returns seconds spent, 0 if skipped.

        Called from ``scripts.seed``, so the load happens under the visible
        "preparing" step rather than inside a learner's first request.
        """
        if not self.available():
            return 0.0
        started = time.perf_counter()
        try:
            httpx.post(
                f"{self.host}/api/generate",
                timeout=WARM_TIMEOUT,
                json={
                    "model": self.model,
                    "prompt": "ok",
                    "stream": False,
                    "keep_alive": KEEP_ALIVE,
                    "options": {"num_predict": 1, "num_ctx": self.num_ctx},
                },
            ).raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("could not warm %s: %s", self.model, str(exc)[:120])
            return 0.0
        elapsed = time.perf_counter() - started
        logger.info("ollama model %s resident after %.1fs", self.model, elapsed)
        return elapsed

    # -- generation ---------------------------------------------------------
    def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_schema: dict | None = None,
    ) -> str:
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
                    # A schema constrains the sampler; "json" only asks politely.
                    "format": json_schema or "json",
                    "keep_alive": KEEP_ALIVE,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                        "num_ctx": self.num_ctx,
                        "num_thread": self.threads,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            text = (payload.get("response") or "").strip()
        except (httpx.HTTPError, ValueError) as exc:
            self._available = None  # the daemon may have stopped; re-probe next time
            raise ProviderUnavailable(f"ollama call failed: {str(exc)[:160]}") from exc

        elapsed = time.perf_counter() - started
        if not text:
            raise ProviderUnavailable("ollama returned an empty completion")
        generated = payload.get("eval_count", 0)
        if generated > 20:
            self._tokens_per_second = generated / max(elapsed, 0.01)
        logger.info(
            "ollama generated %d tokens in %.1fs (%.1f tok/s)",
            generated, elapsed, generated / max(elapsed, 0.01),
        )
        return text
