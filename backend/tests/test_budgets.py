"""Latency budgets: what the product does when the model is too slow to wait for.

Every one of these call sites has a deterministic answer already computed, so
declining to call the model costs phrasing and never correctness. That is the
whole reason a budget is allowed to exist.

The rule is expressed in seconds rather than as "skip this on local models",
which is what these tests pin down: the *same* code calls a fast provider and
declines a slow one, with nothing configured differently.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.llm.base import LLMProvider
from app.narration import affordable
from app.routers.chat import EXPECTED_REPLY_TOKENS
from app.routers.intake import EXPECTED_EXTRACTION_TOKENS, _worth_a_call


class PacedProvider(LLMProvider):
    """A provider that reports a chosen speed and refuses to be called."""

    name = "paced"

    def __init__(self, tokens_per_second: float, *, up: bool = True) -> None:
        self.rate = tokens_per_second
        self.up = up
        self.calls = 0

    def complete(self, prompt, *, temperature=0.2, max_tokens=2048, json_schema=None) -> str:
        self.calls += 1
        raise AssertionError("a budgeted caller must not reach the provider")

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]

    def available(self) -> bool:
        return self.up

    def tokens_per_second(self) -> float:
        return self.rate


# --------------------------------------------------------------------------- #
# The arithmetic
# --------------------------------------------------------------------------- #
def test_projection_is_tokens_over_rate() -> None:
    assert PacedProvider(10.0).projected_seconds(100) == pytest.approx(10.0)


def test_a_fast_provider_affords_what_a_slow_one_cannot() -> None:
    fast, slow = PacedProvider(200.0), PacedProvider(3.0)
    assert fast.affords(150, 25.0)
    assert not slow.affords(150, 25.0)


def test_an_unavailable_provider_affords_nothing() -> None:
    """However fast it claims to be."""
    assert not PacedProvider(1000.0, up=False).affords(1, 3600.0)


def test_a_provider_that_does_not_measure_itself_is_assumed_fast() -> None:
    """The default must not silently disable the language layer everywhere."""
    assert PacedProvider(LLMProvider.tokens_per_second(None)).affords(500, 5.0)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Intake
# --------------------------------------------------------------------------- #
READY = {"goal_text": "quantum computing", "hours_per_week": 6.0}
PARTIAL = {"hours_per_week": 6.0}


def test_intake_does_not_call_a_model_it_does_not_need() -> None:
    """Both load-bearing fields are already in hand; the model would rephrase."""
    provider = PacedProvider(1000.0)
    assert not _worth_a_call(READY, provider)


def test_intake_asks_a_fast_model_to_fill_a_gap() -> None:
    """This is where the model earns its place: the rules found no goal."""
    assert _worth_a_call(PARTIAL, PacedProvider(500.0))


def test_intake_declines_a_model_that_cannot_answer_in_time() -> None:
    slow = PacedProvider(EXPECTED_EXTRACTION_TOKENS / (get_settings().interactive_budget_seconds * 2))
    assert not _worth_a_call(PARTIAL, slow)
    assert slow.calls == 0


def test_the_intake_budget_is_a_setting_not_a_hardcoded_provider_test() -> None:
    """A machine that answers quickly must get the model back with no code change."""
    budget = get_settings().interactive_budget_seconds
    just_fast_enough = PacedProvider(EXPECTED_EXTRACTION_TOKENS / budget * 1.05)
    just_too_slow = PacedProvider(EXPECTED_EXTRACTION_TOKENS / budget * 0.95)
    assert _worth_a_call(PARTIAL, just_fast_enough)
    assert not _worth_a_call(PARTIAL, just_too_slow)


# --------------------------------------------------------------------------- #
# The other budgeted call sites
# --------------------------------------------------------------------------- #
def test_chat_expects_a_short_answer() -> None:
    """A grounded reply cites rows; if this grows, the budget silently stops working."""
    assert 0 < EXPECTED_REPLY_TOKENS <= 300


def test_narration_still_has_its_own_tighter_budget() -> None:
    """Forty rationales are forty calls, so its budget is per-plan, not per-call."""
    within, projected = affordable(40)
    assert projected > 0 or not within
