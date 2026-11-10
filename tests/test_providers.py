from types import SimpleNamespace

import pytest
from strands.types.exceptions import StructuredOutputException

from starsort.models import BatchCategorization, RepoAssignment
from starsort.providers import Provider, ProviderError, build_model, make_asker

GOOD_ANSWER = BatchCategorization(assignments=[RepoAssignment(full_name="a/one", category="AWS")])


class FakeAgent:
    """Mimics a Strands Agent: agent(prompt, structured_output_model=...) -> result.structured_output."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def __call__(self, prompt, structured_output_model=None):
        self.calls.append((prompt, structured_output_model))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return SimpleNamespace(structured_output=self.outcome)


def factory_for(*outcomes):
    agents = [FakeAgent(outcome) for outcome in outcomes]
    created = iter(agents)
    return agents, lambda: next(created)


def test_asker_returns_structured_output_and_passes_schema():
    agents, factory = factory_for(GOOD_ANSWER)

    result = make_asker(model=None, agent_factory=factory)("categorize this")

    assert result == GOOD_ANSWER
    assert agents[0].calls == [("categorize this", BatchCategorization)]


def test_asker_retries_with_a_fresh_agent_after_structured_output_failure():
    agents, factory = factory_for(StructuredOutputException("no tool call"), GOOD_ANSWER)

    result = make_asker(model=None, agent_factory=factory, attempts=2)("prompt")

    assert result == GOOD_ANSWER
    assert len(agents[0].calls) == 1 and len(agents[1].calls) == 1


def test_asker_gives_up_after_all_attempts():
    _, factory = factory_for(StructuredOutputException("fail 1"), StructuredOutputException("fail 2"))

    with pytest.raises(StructuredOutputException):
        make_asker(model=None, agent_factory=factory, attempts=2)("prompt")


def test_anthropic_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        build_model(Provider.anthropic)
