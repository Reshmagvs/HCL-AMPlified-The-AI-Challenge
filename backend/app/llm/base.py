"""The provider contract and the schema-enforcement helper.

Two ideas carry this module.

**A narrow interface.** A provider does exactly two things: ``complete`` a
prompt, and report whether it is ``available``. Anything richer would leak
model-specific behaviour into the callers and make the mock impossible to keep
faithful. Embeddings deliberately do *not* appear here -- they are a local,
deterministic similarity function and live in ``core.embeddings``, so retrieval
quality never depends on an API key.

**Validate, retry once, then fail typed.** ``call_with_schema`` parses the
model's output as JSON, validates it against a Pydantic model, and on failure
re-prompts *once* with the validation error appended -- models correct their own
structural mistakes at a high rate when shown the error. A second failure raises
``SchemaViolation``, which callers catch to degrade deterministically. No caller
ever sees an unhandled parse error, and no unvalidated model output ever reaches
a response body.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class ProviderUnavailable(RuntimeError):
    """The model could not be reached (no key, rate limit, network, 5xx)."""


class SchemaViolation(ValueError):
    """The model replied, but not in the shape the caller requires."""


class LLMProvider(ABC):
    """Minimal capability surface every provider must implement."""

    name: str = "base"

    @abstractmethod
    def complete(self, prompt: str, *, temperature: float = 0.2, max_tokens: int = 2048) -> str:
        """Return the model's raw text for ``prompt``."""

    @abstractmethod
    def available(self) -> bool:
        """True when a live call would plausibly succeed."""


def extract_json(raw: str) -> str:
    """Pull a JSON document out of prose or markdown fences.

    Models wrap JSON in ```json fences and prepend "Here's the result:" often
    enough that stripping both is worth doing before we call it a violation.
    Falls back to the outermost brace/bracket pair.
    """
    text = raw.strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    if text.startswith(("{", "[")):
        return text
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return text
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    return text[start : end + 1] if end > start else text[start:]


def call_with_schema(
    provider: LLMProvider,
    prompt: str,
    schema: type[T],
    *,
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> T:
    """Complete ``prompt`` and coerce the reply into ``schema``.

    Raises ``SchemaViolation`` after one corrective retry, or
    ``ProviderUnavailable`` if the provider itself is down.
    """
    attempt_prompt = prompt
    last_error = ""
    for attempt in (1, 2):
        raw = provider.complete(attempt_prompt, temperature=temperature, max_tokens=max_tokens)
        try:
            return schema.model_validate(json.loads(extract_json(raw)))
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            last_error = str(exc)[:500]
            logger.warning("schema attempt %d failed for %s: %s", attempt, schema.__name__, last_error)
            attempt_prompt = (
                f"{prompt}\n\n"
                f"Your previous reply could not be parsed. Error:\n{last_error}\n"
                "Reply with valid JSON only -- no prose, no markdown fences."
            )
    raise SchemaViolation(f"{schema.__name__} validation failed twice: {last_error}")
