"""The one place a model is asked to turn computed data into English.

This module sits deliberately *outside* ``core/``. The layering rule is that the
reasoning layer never imports the language layer, so ``core.explain`` builds the
provenance record and renders the deterministic template, and this thin adapter
is what decides whether to ask a model to phrase it more naturally.

The model receives the provenance object and nothing else -- no conversation, no
catalog, no learner history. With no open context it cannot state a reason the
data does not support. If it fails schema validation twice, or the provider is
down, the template output is used and the caller is told the response is
degraded. **The reason always exists; only its polish is optional.**
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.explain import render_template
from app.llm import get_provider
from app.llm.base import ProviderUnavailable, SchemaViolation, call_with_schema
from app.llm.prompts import RATIONALE_NARRATION

logger = logging.getLogger(__name__)

MAX_RATIONALE_CHARS = 400

# Roughly what one rationale costs to generate, measured across real calls.
TOKENS_PER_RATIONALE = 90


class Rationale(BaseModel):
    rationale: str = Field(min_length=10, max_length=600)


def narrate(provenance: dict[str, Any]) -> tuple[str, bool]:
    """Return (two sentences, degraded). Never raises."""
    fallback = render_template(provenance)
    provider = get_provider()
    if not provider.available():
        return fallback, True

    prompt = RATIONALE_NARRATION.format(provenance=json.dumps(provenance, indent=1))
    try:
        result = call_with_schema(provider, prompt, Rationale, temperature=0.4, max_tokens=300)
    except (SchemaViolation, ProviderUnavailable) as exc:
        logger.info("rationale narration degraded for %s: %s", provenance.get("skill"), str(exc)[:120])
        return fallback, True
    return result.rationale.strip()[:MAX_RATIONALE_CHARS], False


def affordable(item_count: int) -> tuple[bool, float]:
    """Whether narrating this many items fits the latency budget, and the estimate.

    The provider reports how fast it generates and this does the arithmetic. A
    hosted model at a few hundred tokens a second narrates a forty-item plan in
    a few seconds and should. The local 3B model on a laptop CPU manages three,
    which turns the same plan into six minutes of a learner staring at a
    spinner -- for prose that only rephrases a reason the planner already
    computed and already displays.

    This is why the rule is a budget rather than "skip narration on local
    models": the same code does the right thing on a machine with a GPU, or with
    a faster model, without anyone changing a setting.
    """
    provider = get_provider()
    if not provider.available():
        return False, 0.0
    tokens = item_count * TOKENS_PER_RATIONALE
    budget = get_settings().narration_budget_seconds
    # Through the provider so that the queue counts too: a plan narrated while
    # a subject is being built waits for the build first, and the arithmetic
    # over tokens alone cannot see that.
    return provider.affords(tokens, budget), provider.projected_seconds(tokens)


def narrate_all(provenances: list[dict[str, Any]]) -> tuple[list[str], bool]:
    """Narrate a whole plan, or render all of it deterministically.

    Two short-circuits. If narration cannot fit the latency budget the whole
    plan is templated without a single call being made. And once a call has
    failed there is no value in making twenty-nine more that will fail the same
    way, so the first failure switches the rest to the renderer.
    """
    if not provenances:
        return [], False

    within_budget, projected = affordable(len(provenances))
    if not within_budget:
        logger.info(
            "narrating %d items would take about %.0fs at %.1f tok/s -- using templates",
            len(provenances), projected, get_provider().tokens_per_second(),
        )
        return [render_template(p) for p in provenances], True

    texts: list[str] = []
    degraded = False
    for provenance in provenances:
        if degraded:
            texts.append(render_template(provenance))
            continue
        text, failed = narrate(provenance)
        texts.append(text)
        degraded = degraded or failed
    return texts, degraded
