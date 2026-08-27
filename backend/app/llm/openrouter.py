"""Hosted generation through OpenRouter, restricted to models that cost nothing.

The local model is the thing this product runs on, and it stays. But on a
four-core laptop it decodes at three to four tokens a second, and the latency
budgets in ``app.config`` correctly refuse to put that in front of someone
waiting for a reply -- so the conversational layer fell back to templates, and a
learner who typed something the rules could not parse saw the same sentence
again. Templates cannot converse.

OpenRouter fixes that without a bill. Measured on the same prompt the local
model takes forty-three seconds on, ``nemotron-3-super-120b`` answered in
**3.5 seconds** at zero cost.

Three rules make "free" a property of the code rather than a hope:

**The model list is discovered, never hardcoded.** ``/api/v1/models`` is
filtered to entries whose prompt *and* completion price are exactly zero, which
emit text, and which support structured output. A model that stops being free
stops being selected the next time the list refreshes, without anyone editing a
constant.

**Every response is checked for cost.** OpenRouter reports ``usage.cost`` per
call. A non-zero cost means the model is no longer free, so it is dropped
permanently for this process and the next candidate takes over. The guarantee
is enforced after the fact as well as before it.

**A rate-limited model stands down rather than failing the request.** Free
models are individually and unpredictably throttled -- ``glm-5.2:free`` returned
HTTP 429 during development while its neighbour answered fine. So candidates
form a chain with cooldowns, exactly as the search sources in
``core.websearch`` do, and only an exhausted chain raises.

Usage is tracked per process and exposed through ``/api/usage`` so the
interface can show what has been spent. What is *not* done is invent a
denominator: OpenRouter reports no daily request cap for a free-tier key, so
the interface shows real counts and says the cap is unpublished rather than
drawing a progress bar against a number nobody stated.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import get_settings
from app.llm.base import LLMProvider, ProviderUnavailable

logger = logging.getLogger(__name__)

API_ROOT = "https://openrouter.ai/api/v1"
MODELS_TIMEOUT = 20.0
GENERATE_TIMEOUT = 120.0
KEY_TIMEOUT = 15.0

# How long a model that refused sits out before it is tried again. Free-tier
# throttling is usually measured in minutes, so this is deliberately short: the
# aim is to route around a busy model, not to blacklist it.
COOLDOWN_SECONDS = 300.0

# The model list changes rarely and costs a round trip, so it is cached.
CATALOGUE_TTL = 1800.0

# Sent so OpenRouter can attribute traffic; both are optional in their API and
# neither carries anything about the learner.
REFERER = "https://github.com/retr0alfred/PathFinder"
TITLE = "Lodestar"

# Free models differ enormously in how well they hold a structure. Ranking is
# by properties the catalogue actually reports -- never by a hand-kept list of
# favourites, which would rot the first time a provider retired a model.
def _rank(model: dict[str, Any]) -> tuple:
    """Sort key: structured-output support first, then context, then id."""
    supported = model.get("supported_parameters") or []
    return (
        0 if "structured_outputs" in supported else 1,
        0 if "response_format" in supported else 1,
        -(model.get("context_length") or 0),
        model.get("id", ""),
    )


def _is_free(model: dict[str, Any]) -> bool:
    """Zero for both halves of the price. Anything unparseable is not free."""
    pricing = model.get("pricing") or {}
    try:
        return float(pricing.get("prompt", 1)) == 0.0 and float(pricing.get("completion", 1)) == 0.0
    except (TypeError, ValueError):
        return False


def _emits_text(model: dict[str, Any]) -> bool:
    """Excludes the image and audio models that are also priced at zero."""
    return "text" in ((model.get("architecture") or {}).get("output_modalities") or ["text"])


@dataclass
class Usage:
    """What this process has spent. Reported to the interface verbatim."""

    requests: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    started_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "failures": self.failures,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "cost": round(self.cost, 6),
            "since": self.started_at,
        }


class OpenRouterProvider(LLMProvider):
    """Generation via OpenRouter, over free models only."""

    name = "openrouter"

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.openrouter_api_key.strip()
        self.preferred = settings.openrouter_model.strip()
        self._catalogue: list[str] = []
        self._catalogue_at = 0.0
        self._cooldowns: dict[str, float] = {}
        self._retired: set[str] = set()
        self._lock = threading.Lock()
        # Before any call has been measured. Hosted free models have run
        # between 20 and 110 tok/s here; 60 is deliberately below the middle so
        # the first request of a process is not promised speed it may not have,
        # while still being realistic enough that latency budgets do not
        # reject work this provider can comfortably do.
        self._tokens_per_second = 60.0
        self.usage = Usage()
        self.last_model = ""

    # -- capability ---------------------------------------------------------
    def available(self) -> bool:
        """True when a key is configured and some free model is not cooling."""
        return bool(self.api_key) and bool(self._candidates())

    def tokens_per_second(self) -> float:
        """Measured on the most recent completion."""
        return self._tokens_per_second

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": REFERER,
            "X-Title": TITLE,
        }

    # -- model selection ----------------------------------------------------
    def _refresh_catalogue(self) -> None:
        """Re-read the free, text-emitting models. Failures keep the old list."""
        try:
            response = httpx.get(f"{API_ROOT}/models", timeout=MODELS_TIMEOUT)
            response.raise_for_status()
            models = response.json().get("data", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("could not refresh the OpenRouter catalogue: %s", str(exc)[:120])
            return

        usable = [m for m in models if _is_free(m) and _emits_text(m)]
        usable.sort(key=_rank)
        self._catalogue = [m["id"] for m in usable if m.get("id")]
        self._catalogue_at = time.monotonic()
        logger.info(
            "openrouter: %d free text models available, preferring %s",
            len(self._catalogue), self._catalogue[0] if self._catalogue else "nothing",
        )

    def _candidates(self) -> list[str]:
        """Free models to try, in order, excluding cooling and retired ones."""
        if not self.api_key:
            return []
        with self._lock:
            stale = time.monotonic() - self._catalogue_at > CATALOGUE_TTL
            if not self._catalogue or stale:
                self._refresh_catalogue()
            now = time.monotonic()
            ordered = list(self._catalogue)
            # A configured model goes first, but only if the catalogue agrees it
            # is free -- the setting cannot be used to smuggle in a paid model.
            if self.preferred and self.preferred in ordered:
                ordered.remove(self.preferred)
                ordered.insert(0, self.preferred)
            elif self.preferred:
                logger.warning(
                    "OPENROUTER_MODEL=%s is not a free model -- ignoring it", self.preferred
                )
            return [
                m for m in ordered
                if m not in self._retired and self._cooldowns.get(m, 0.0) < now
            ]

    def _stand_down(self, model: str, reason: str) -> None:
        with self._lock:
            self._cooldowns[model] = time.monotonic() + COOLDOWN_SECONDS
        logger.info("openrouter: %s standing down for %.0fs (%s)",
                    model, COOLDOWN_SECONDS, reason[:80])

    def _retire(self, model: str, reason: str) -> None:
        with self._lock:
            self._retired.add(model)
        logger.warning("openrouter: %s retired permanently (%s)", model, reason[:100])

    # -- generation ---------------------------------------------------------
    def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_schema: dict | None = None,
    ) -> str:
        """One completion, trying each free model until one answers."""
        if not self.api_key:
            raise ProviderUnavailable("OPENROUTER_API_KEY is not set")

        candidates = self._candidates()
        if not candidates:
            raise ProviderUnavailable("every free OpenRouter model is cooling down")

        last_error = "no model was tried"
        for model in candidates:
            try:
                return self._call(model, prompt, temperature, max_tokens, json_schema)
            except ProviderUnavailable as exc:
                last_error = str(exc)
                continue
        self.usage.failures += 1
        raise ProviderUnavailable(f"no free OpenRouter model answered: {last_error[:160]}")

    def _call(
        self, model: str, prompt: str, temperature: float,
        max_tokens: int, json_schema: dict | None,
    ) -> str:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Several of the best free models are reasoning models, and every
            # reasoning token is spent before the answer starts. Measured on
            # the same extraction: reasoning on cost 700 tokens and returned a
            # truncated, malformed object; reasoning off cost 39 and returned a
            # clean one in 2.3 s. Nothing here needs a model to think out loud
            # -- the reasoning in this product is done by the graph.
            "reasoning": {"enabled": False},
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "strict": True, "schema": json_schema},
            }

        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{API_ROOT}/chat/completions",
                headers=self._headers(), json=body, timeout=GENERATE_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            self._stand_down(model, f"transport: {exc}")
            raise ProviderUnavailable(f"{model}: {str(exc)[:120]}") from exc

        if response.status_code == 401:
            # A bad key is not a per-model problem and retrying every candidate
            # would just be 20 identical rejections.
            self.api_key = ""
            raise ProviderUnavailable("OpenRouter rejected the API key")
        if response.status_code in (402, 429) or response.status_code >= 500:
            self._stand_down(model, f"HTTP {response.status_code}")
            raise ProviderUnavailable(f"{model}: HTTP {response.status_code}")
        if response.status_code != 200:
            self._stand_down(model, f"HTTP {response.status_code}")
            raise ProviderUnavailable(f"{model}: HTTP {response.status_code} {response.text[:120]}")

        try:
            payload = response.json()
        except ValueError as exc:
            self._stand_down(model, "unparseable body")
            raise ProviderUnavailable(f"{model}: reply was not JSON") from exc

        # A free model that started charging is retired, not merely rested.
        usage = payload.get("usage") or {}
        cost = float(usage.get("cost") or 0.0)
        if cost > 0:
            self._retire(model, f"reported a cost of {cost}")
            raise ProviderUnavailable(f"{model} is no longer free")

        choices = payload.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        text = (message.get("content") or "").strip()
        if not text:
            self._stand_down(model, "empty completion")
            raise ProviderUnavailable(f"{model}: empty completion")

        elapsed = time.perf_counter() - started
        generated = int(usage.get("completion_tokens") or 0)
        if generated > 20 and elapsed > 0:
            # A running average, not the last sample. Throughput on a shared
            # free model varies by an order of magnitude between a 40-token
            # extraction and a 1500-token syllabus, and letting one slow call
            # set the figure meant the next decision was made on an outlier --
            # a plan would decline narration purely because the syllabus before
            # it happened to be slow. Weighted towards recent calls, because
            # a model that has started throttling matters more than one that
            # was fast ten minutes ago.
            observed = generated / elapsed
            self._tokens_per_second = 0.6 * observed + 0.4 * self._tokens_per_second

        self.usage.requests += 1
        self.usage.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.usage.completion_tokens += generated
        self.usage.cost += cost
        self.last_model = model
        logger.info(
            "openrouter: %s answered in %.1fs (%d tokens, cost %.6f)",
            model, elapsed, generated, cost,
        )
        return text

    # -- reporting ----------------------------------------------------------
    def account(self) -> dict[str, Any]:
        """What OpenRouter says about this key. Never guesses a missing figure."""
        if not self.api_key:
            return {"configured": False}
        try:
            response = httpx.get(f"{API_ROOT}/key", headers=self._headers(), timeout=KEY_TIMEOUT)
            response.raise_for_status()
            data = response.json().get("data", {})
        except (httpx.HTTPError, ValueError) as exc:
            return {"configured": True, "reachable": False, "error": str(exc)[:120]}
        return {
            "configured": True,
            "reachable": True,
            "free_tier": bool(data.get("is_free_tier")),
            # None means OpenRouter states no limit for this key. It is passed
            # through as null rather than turned into a plausible number.
            "credit_limit": data.get("limit"),
            "credit_used": data.get("usage"),
            "credit_used_today": data.get("usage_daily"),
        }

    def status(self) -> dict[str, Any]:
        """Everything the usage panel needs, in one call."""
        candidates = self._candidates()
        with self._lock:
            cooling = sorted(
                m for m, until in self._cooldowns.items() if until >= time.monotonic()
            )
            retired = sorted(self._retired)
        return {
            "provider": self.name,
            "model": self.last_model or (candidates[0] if candidates else ""),
            "free_models_available": len(candidates),
            "cooling_down": cooling,
            "retired": retired,
            "tokens_per_second": round(self._tokens_per_second, 1),
            "session": self.usage.as_dict(),
        }
