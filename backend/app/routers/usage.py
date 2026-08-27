"""What the language layer has spent, and what is answering right now.

Exists because "am I about to run out?" is a fair question to be able to answer
without reading logs, and because a hosted free tier makes it a real one.

The one design rule here is that nothing is invented. OpenRouter reports no
daily request cap for a free-tier key, so this endpoint does not manufacture a
denominator to draw a progress bar against -- it reports the counts that are
real (requests made, tokens spent, cost incurred, which models are cooling
down) and states plainly that the cap is unpublished. A bar that said "12% of
your daily limit" would be a number nobody measured.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.llm import ChainProvider, get_provider

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("")
def usage() -> dict:
    """Provider state and spend. Safe to poll; the account call is cached by the client."""
    provider = get_provider()
    chain = provider if isinstance(provider, ChainProvider) else None

    active = chain.active if chain else provider
    payload: dict = {
        "provider": active.name if active else "none",
        "chain": [p.name for p in chain.providers] if chain else [provider.name],
        "tokens_per_second": round(active.tokens_per_second(), 1) if active else 0.0,
        "openrouter": None,
    }

    # The hosted provider is the only one with anything to run out of. The
    # local model has no quota and the offline one has no cost.
    candidates = chain.providers if chain else [provider]
    hosted = next((p for p in candidates if p.name == "openrouter"), None)
    if hosted is not None:
        status = hosted.status()
        status["account"] = hosted.account()
        # Free models publish no per-day request allowance, so there is no
        # denominator to show. Saying so is more useful than a made-up bar.
        status["limit_published"] = status["account"].get("credit_limit") is not None
        payload["openrouter"] = status

    return payload
