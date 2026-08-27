"""The hosted provider, and the guarantee that it only ever costs nothing.

No network. What is tested is the selection logic and the cost guard, because
those are the parts that decide whether a key can start being billed -- and
those are exactly the parts that would be discovered too late in production.

The chain is tested here too. Its whole reason for existing is that free models
are throttled without warning, so "the first one refused and the second
answered" is the behaviour, not an edge case.
"""

from __future__ import annotations

import pytest

from app.llm import ChainProvider
from app.llm.base import LLMProvider, ProviderUnavailable
from app.llm.openrouter import OpenRouterProvider, _emits_text, _is_free, _rank


def model(mid: str, prompt="0", completion="0", out=("text",), params=("structured_outputs",),
          ctx=100_000) -> dict:
    return {
        "id": mid,
        "pricing": {"prompt": prompt, "completion": completion},
        "architecture": {"output_modalities": list(out)},
        "supported_parameters": list(params),
        "context_length": ctx,
    }


# --------------------------------------------------------------------------- #
# "Free" is a property of the data, not a list we maintain
# --------------------------------------------------------------------------- #
def test_only_models_priced_at_zero_count_as_free() -> None:
    assert _is_free(model("a"))
    assert not _is_free(model("b", prompt="0.0000001"))
    assert not _is_free(model("c", completion="0.5"))


def test_an_unparseable_price_is_not_free() -> None:
    """The safe reading of a missing or malformed price is "this might cost"."""
    assert not _is_free({"pricing": {"prompt": None, "completion": "0"}})
    assert not _is_free({})


def test_models_that_do_not_emit_text_are_excluded() -> None:
    """Several zero-priced models are image or audio generators."""
    assert _emits_text(model("a"))
    assert not _emits_text(model("b", out=("audio",)))


def test_structured_output_support_is_preferred() -> None:
    """Every load-bearing call in this product asks for a schema."""
    structured = model("s", params=("structured_outputs", "response_format"))
    loose = model("l", params=(), ctx=999_999)
    assert _rank(structured) < _rank(loose)


# --------------------------------------------------------------------------- #
# The cost guard
# --------------------------------------------------------------------------- #
def test_a_model_that_reports_a_cost_is_retired(monkeypatch) -> None:
    """The promise is enforced after the call as well as before it.

    A model can be listed as free and bill anyway -- a pricing change, a
    mis-tagged entry. Checking the reported cost of every response is what
    makes "free models only" something the code guarantees rather than
    something the catalogue promises.
    """
    provider = OpenRouterProvider()
    provider.api_key = "test-key"

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"cost": 0.0002, "completion_tokens": 50},
            }

    monkeypatch.setattr("app.llm.openrouter.httpx.post", lambda *a, **k: Response())

    with pytest.raises(ProviderUnavailable, match="no longer free"):
        provider._call("pricey/model", "hi", 0.2, 100, None)
    assert "pricey/model" in provider._retired
    assert provider.usage.cost == 0.0


def test_a_retired_model_is_never_offered_again(monkeypatch) -> None:
    provider = OpenRouterProvider()
    provider.api_key = "test-key"
    provider._catalogue = ["a:free", "b:free"]
    provider._catalogue_at = float("inf")
    provider._retired.add("a:free")
    assert provider._candidates() == ["b:free"]


def test_a_configured_model_the_catalogue_does_not_call_free_is_ignored() -> None:
    """The setting must not be a way to start spending money."""
    provider = OpenRouterProvider()
    provider.api_key = "test-key"
    provider.preferred = "expensive/model"
    provider._catalogue = ["a:free", "b:free"]
    provider._catalogue_at = float("inf")
    assert provider._candidates() == ["a:free", "b:free"]


def test_a_configured_free_model_goes_first() -> None:
    provider = OpenRouterProvider()
    provider.api_key = "test-key"
    provider.preferred = "b:free"
    provider._catalogue = ["a:free", "b:free"]
    provider._catalogue_at = float("inf")
    assert provider._candidates()[0] == "b:free"


def test_no_key_means_unavailable_rather_than_an_error() -> None:
    provider = OpenRouterProvider()
    provider.api_key = ""
    assert not provider.available()


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #
class Stub(LLMProvider):
    def __init__(self, name: str, *, up: bool = True, raises: bool = False, rate: float = 50.0):
        self.name = name
        self.up = up
        self.raises = raises
        self.rate = rate
        self.calls = 0

    def complete(self, prompt, *, temperature=0.2, max_tokens=2048, json_schema=None) -> str:
        self.calls += 1
        if self.raises:
            raise ProviderUnavailable(f"{self.name} is busy")
        return f"answer from {self.name}"

    def available(self) -> bool:
        return self.up

    def tokens_per_second(self) -> float:
        return self.rate


def test_the_chain_falls_through_a_throttled_provider() -> None:
    """The behaviour free models make routine, not an edge case."""
    hosted, local = Stub("openrouter", raises=True), Stub("ollama")
    assert ChainProvider([hosted, local]).complete("hi") == "answer from ollama"
    assert hosted.calls == 1 and local.calls == 1


def test_an_unavailable_provider_is_skipped_without_being_called() -> None:
    hosted, local = Stub("openrouter", up=False), Stub("ollama")
    assert ChainProvider([hosted, local]).complete("hi") == "answer from ollama"
    assert hosted.calls == 0


def test_the_chain_reports_the_speed_of_whoever_would_answer() -> None:
    """Latency budgets are only honest if they ask the right provider."""
    chain = ChainProvider([Stub("openrouter", up=False, rate=200.0), Stub("ollama", rate=4.0)])
    assert chain.tokens_per_second() == 4.0
    assert chain.describe() == "ollama"


def test_a_chain_with_nothing_left_raises_rather_than_returning_empty() -> None:
    chain = ChainProvider([Stub("a", raises=True), Stub("b", raises=True)])
    with pytest.raises(ProviderUnavailable):
        chain.complete("hi")


def test_the_chain_is_unavailable_only_when_every_link_is() -> None:
    assert not ChainProvider([Stub("a", up=False), Stub("b", up=False)]).available()
    assert ChainProvider([Stub("a", up=False), Stub("b")]).available()
