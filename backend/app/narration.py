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

from app.core.explain import render_template
from app.llm import get_provider
from app.llm.base import ProviderUnavailable, SchemaViolation, call_with_schema
from app.llm.prompts import RATIONALE_NARRATION

logger = logging.getLogger(__name__)

MAX_RATIONALE_CHARS = 400


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


def narrate_all(provenances: list[dict[str, Any]]) -> tuple[list[str], bool]:
    """Narrate a whole plan, short-circuiting to templates once the provider fails.

    A path can hold thirty items. Once one call has failed there is no value in
    making twenty-nine more that will fail the same way and cost the learner
    thirty seconds of latency, so the first failure switches the rest to the
    deterministic renderer.
    """
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
