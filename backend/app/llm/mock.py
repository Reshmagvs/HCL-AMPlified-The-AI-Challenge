"""Offline provider: deterministic, network-free, and deliberately faithful.

This is not a stub. The entire product -- intake, diagnostic, planning,
explanation and chat -- must work with no API key, because that is both the test
environment and the honest failure mode in production. So the mock answers every
prompt type with well-formed JSON in the same shape the real model returns.

Two design choices make it useful rather than merely present:

* **Embeddings are a hashing vectoriser, not noise.** Tokens (words plus
  character trigrams) are hashed into ``embedding_dim`` buckets with a signed
  count, then L2-normalised. Cosine similarity therefore tracks real lexical
  overlap, so goal resolution against the skill nodes returns sensible matches
  offline instead of arbitrary ones. Identical text always yields an identical
  vector, which is what the determinism tests rely on.
* **It never invents a resource.** The catalog-harvest prompt returns an empty
  list. A fabricated URL from the mock would be indistinguishable from a
  fabricated URL from the real model, and the whole catalog pipeline exists to
  make that impossible.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from typing import Any

from app.config import get_settings
from app.core.text_profile import extract_profile, next_question
from app.llm import prompts
from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    """Words plus character trigrams, so near-misses still overlap."""
    words = _TOKEN_RE.findall(text.lower())
    grams = [w[i : i + 3] for w in words if len(w) > 3 for i in range(len(w) - 2)]
    return words + grams


def _stable_hash(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


class MockProvider(LLMProvider):
    """A first-class provider that happens to require no network."""

    name = "mock"

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or get_settings().embedding_dim

    # -- capability ---------------------------------------------------------
    def available(self) -> bool:
        """Always reachable. ``/health`` reports the provider name separately."""
        return True

    # -- embeddings ---------------------------------------------------------
    def embed(self, text: str) -> list[float]:
        """Signed hashing vectoriser, L2-normalised.

        Word tokens carry more weight than trigrams so that an exact term match
        dominates a coincidental substring match.
        """
        vector = [0.0] * self.dim
        words = _TOKEN_RE.findall(text.lower())
        for token in _tokens(text):
            digest = _stable_hash(token)
            index = digest % self.dim
            sign = 1.0 if (digest >> 61) & 1 else -1.0
            vector[index] += sign * (2.0 if token in words else 0.6)
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]

    # -- generation ---------------------------------------------------------
    def complete(self, prompt: str, *, temperature: float = 0.2, max_tokens: int = 2048) -> str:
        """Dispatch on the marker each prompt template carries."""
        handlers = (
            (prompts.MARK_INTAKE, self._intake),
            (prompts.MARK_GOAL, self._goal),
            (prompts.MARK_QUIZ, self._quiz),
            (prompts.MARK_RATIONALE, self._rationale),
            (prompts.MARK_CHAT, self._chat),
            (prompts.MARK_HARVEST, self._harvest),
        )
        for marker, handler in handlers:
            if marker in prompt:
                return json.dumps(handler(prompt))
        return json.dumps({"reply": "Mock provider: no canned response for this prompt."})

    # -- canned responses ---------------------------------------------------
    @staticmethod
    def _section(prompt: str, header: str) -> str:
        """Read the text following ``header:`` up to the next blank line."""
        match = re.search(rf"^{re.escape(header)}:\s*(.*?)(?:\n\s*\n|\Z)", prompt, re.S | re.M)
        return match.group(1).strip() if match else ""

    def _intake(self, prompt: str) -> dict[str, Any]:
        transcript = self._section(prompt, "CONVERSATION SO FAR")
        learner_lines = [
            line.split(":", 1)[1].strip()
            for line in transcript.splitlines()
            if line.lower().startswith(("learner:", "user:"))
        ]
        profile = extract_profile("\n".join(learner_lines) or transcript)
        return {"assistant_message": next_question(profile), "profile": profile}

    def _goal(self, prompt: str) -> dict[str, Any]:
        """Pick the highest-scoring candidate; the router ranks them by cosine."""
        ids = re.findall(r"^\s*-\s*([a-z0-9_.]+)\s*\|", prompt, re.M)
        return {
            "skill_ids": ids[:1],
            "reason": "Closest match to the stated goal by embedding similarity.",
        }

    def _quiz(self, prompt: str) -> dict[str, Any]:
        skill = self._section(prompt, "SKILL") or "this skill"
        keywords = self._section(prompt, "KEY IDEAS") or "the core ideas"
        answer_index = _stable_hash(skill) % 4
        options = [
            f"It is unrelated to {skill.lower()} in practice.",
            f"It only matters for very large datasets, never for small ones.",
            f"It is a naming convention with no effect on behaviour.",
            f"It is a core mechanism of {skill.lower()}, built on {keywords.split(',')[0]}.",
        ]
        correct = options.pop()
        options.insert(answer_index, correct)
        return {
            "question": f"Which statement best describes the role of {skill} in practice?",
            "options": options,
            "answer_index": answer_index,
            "explanation": f"{skill} is defined by that mechanism.",
        }

    def _rationale(self, prompt: str) -> dict[str, Any]:
        """Narrate using the same template the deterministic fallback uses."""
        from app.core.explain import render_template

        block = self._section(prompt, "PROVENANCE")
        try:
            return {"rationale": render_template(json.loads(block))}
        except (json.JSONDecodeError, KeyError, TypeError):
            return {"rationale": "This step closes a gap between your current level and your goal."}

    def _chat(self, prompt: str) -> dict[str, Any]:
        context = self._section(prompt, "LEARNER CONTEXT")
        question = self._section(prompt, "QUESTION")
        head = " ".join(line.strip() for line in context.splitlines()[:4] if line.strip())
        if not head:
            return {"reply": "I do not have your path data loaded yet, so I cannot answer that."}
        return {
            "reply": (
                f"Working from your plan only: {head[:400]} "
                f"That is what the data says about \"{question[:80]}\"."
            )
        }

    def _harvest(self, _prompt: str) -> dict[str, Any]:
        """Never propose a URL offline -- a guessed link is the failure we design out."""
        logger.info("mock provider declines to propose catalog candidates")
        return {"resources": []}
