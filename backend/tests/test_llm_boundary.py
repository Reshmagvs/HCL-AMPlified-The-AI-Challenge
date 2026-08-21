"""Phase 3 acceptance: the language layer degrades, it never explodes.

The contract these tests pin down is narrow and load-bearing: a caller asking
for structured output either gets a validated object, or gets `SchemaViolation`
/ `ProviderUnavailable` -- both of which every caller already handles by falling
back to deterministic behaviour. An unhandled `JSONDecodeError` reaching a
router would become a 500, and the brief allows none.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from app.llm.base import (
    LLMProvider,
    ProviderUnavailable,
    SchemaViolation,
    call_with_schema,
    extract_json,
)
from app.llm.mock import MockProvider


class Shape(BaseModel):
    name: str
    count: int


class ScriptedProvider(LLMProvider):
    """Returns a fixed list of replies in order, recording how often it was called."""

    name = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[str] = []

    def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_schema: dict | None = None,
    ) -> str:
        self.calls.append(prompt)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]

    def available(self) -> bool:
        return True


# --------------------------------------------------------------------------- #
# JSON extraction
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    [
        '{"name": "a", "count": 1}',
        '```json\n{"name": "a", "count": 1}\n```',
        '```\n{"name": "a", "count": 1}\n```',
        'Sure! Here is the result:\n{"name": "a", "count": 1}\nHope that helps.',
        '   \n{"name": "a", "count": 1}\n\n',
    ],
)
def test_extract_json_survives_fences_and_prose(raw: str) -> None:
    assert json.loads(extract_json(raw)) == {"name": "a", "count": 1}


# --------------------------------------------------------------------------- #
# call_with_schema
# --------------------------------------------------------------------------- #
def test_valid_output_returns_on_first_call() -> None:
    provider = ScriptedProvider(['{"name": "a", "count": 2}'])
    assert call_with_schema(provider, "p", Shape) == Shape(name="a", count=2)
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "bad",
    [
        '{"name": "a"}',                      # missing field
        '{"name": "a", "count": "many"}',     # wrong type
        "not json at all",                    # unparseable
        "",                                   # empty
        '{"name": "a", "count": 1',           # truncated
    ],
)
def test_malformed_output_retries_once_then_raises(bad: str) -> None:
    provider = ScriptedProvider([bad])
    with pytest.raises(SchemaViolation):
        call_with_schema(provider, "p", Shape)
    assert len(provider.calls) == 2, "exactly one corrective retry"
    assert "could not be parsed" in provider.calls[1]


def test_retry_succeeds_when_the_model_corrects_itself() -> None:
    provider = ScriptedProvider(['{"name": "a"}', '{"name": "a", "count": 3}'])
    assert call_with_schema(provider, "p", Shape).count == 3
    assert len(provider.calls) == 2


def test_provider_unavailable_propagates_without_retrying() -> None:
    class Dead(ScriptedProvider):
        def complete(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_schema: dict | None = None,
    ) -> str:
            self.calls.append(prompt)
            raise ProviderUnavailable("down")

    provider = Dead([])
    with pytest.raises(ProviderUnavailable):
        call_with_schema(provider, "p", Shape)
    assert len(provider.calls) == 1


# --------------------------------------------------------------------------- #
# The mock is a real implementation, not a stub
#
# Embeddings are not tested here: they are no longer a provider concern at all.
# See test_embeddings.py.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("marker_prompt", "expected_key"),
    [
        ("intake", "assistant_message"),
        ("goal", "skill_ids"),
        ("quiz", "question"),
        ("chat", "reply"),
        ("harvest", "resources"),
    ],
)
def test_mock_answers_every_prompt_type_with_valid_json(marker_prompt, expected_key) -> None:
    from app.llm import prompts

    templates = {
        "intake": prompts.INTAKE_EXTRACTION.format(transcript="Learner: hi", profile="{}"),
        "goal": prompts.GOAL_RESOLUTION.format(
            goal_text="become an ml engineer", candidates="  - ml.engineer | Machine Learning Engineer"
        ),
        "quiz": prompts.QUIZ_GENERATION.format(
            skill_name="Gradient Descent", skill_description="d", keywords="a, b", difficulty=3
        ),
        "chat": prompts.CHAT_GROUNDED.format(context="Week 1: Python Basics", question="what is next?"),
        "harvest": prompts.HARVEST_CANDIDATES.format(
            skill_name="X", skill_id="x", skill_description="d", keywords="k", difficulty=2
        ),
    }
    payload = json.loads(MockProvider().complete(templates[marker_prompt]))
    assert expected_key in payload


def test_mock_never_proposes_a_resource() -> None:
    """A fabricated URL from the mock is as harmful as one from the real model."""
    from app.llm import prompts

    prompt = prompts.HARVEST_CANDIDATES.format(
        skill_name="X", skill_id="x", skill_description="d", keywords="k", difficulty=2
    )
    assert json.loads(MockProvider().complete(prompt))["resources"] == []


def test_mock_quiz_is_deterministic_and_well_formed() -> None:
    from app.llm import prompts

    prompt = prompts.QUIZ_GENERATION.format(
        skill_name="Backpropagation", skill_description="d", keywords="chain rule", difficulty=4
    )
    provider = MockProvider()
    first = json.loads(provider.complete(prompt))
    assert first == json.loads(provider.complete(prompt))
    assert len(first["options"]) == 4
    assert 0 <= first["answer_index"] <= 3


def test_every_prompt_lives_in_the_prompts_module() -> None:
    """No prompt literal may exist outside app/llm/prompts.py."""
    from pathlib import Path

    app_dir = Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        if path.name == "prompts.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "Return JSON" in text or "Output JSON only" in text:
            offenders.append(str(path.relative_to(app_dir)))
    assert offenders == [], f"prompt text found outside prompts.py: {offenders}"
