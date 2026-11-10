import os
from collections.abc import Callable
from enum import Enum
from typing import Any

from starsort.categorizer import SYSTEM_PROMPT, Asker
from starsort.models import BatchCategorization

MAX_OUTPUT_TOKENS = 16000


class Provider(str, Enum):
    bedrock = "bedrock"
    anthropic = "anthropic"


DEFAULT_MODELS = {
    Provider.bedrock: "us.anthropic.claude-opus-5-5",
    Provider.anthropic: "claude-opus-5-5",
}


class ProviderError(Exception):
    pass


def build_model(provider: Provider, model_id: str | None = None) -> Any:
    model_id = model_id or DEFAULT_MODELS[provider]

    if provider is Provider.bedrock:
        from strands.models import BedrockModel

        region = os.environ.get("AWS_REGION")
        region_args = {"region_name": region} if region else {}
        return BedrockModel(model_id=model_id, max_tokens=MAX_OUTPUT_TOKENS, **region_args)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ProviderError("Set ANTHROPIC_API_KEY in .env to use --provider anthropic.")
    try:
        from strands.models.anthropic import AnthropicModel
    except ImportError as error:
        raise ProviderError("The Anthropic provider is optional. Install it with: uv sync --extra anthropic") from error
    return AnthropicModel(client_args={"api_key": api_key}, model_id=model_id, max_tokens=MAX_OUTPUT_TOKENS)


def make_asker(model: Any, agent_factory: Callable[[], Any] | None = None, attempts: int = 2) -> Asker:
    """Wrap a Strands agent as a function: prompt in, validated BatchCategorization out."""
    from strands import Agent
    from strands.types.exceptions import StructuredOutputException

    def new_agent() -> Any:
        return Agent(model=model, system_prompt=SYSTEM_PROMPT, callback_handler=None)

    factory = agent_factory or new_agent

    def ask(prompt: str) -> BatchCategorization:
        last_error: StructuredOutputException | None = None
        for _ in range(attempts):
            agent = factory()  # fresh agent per call, so batches never share conversation history
            try:
                return agent(prompt, structured_output_model=BatchCategorization).structured_output
            except StructuredOutputException as error:
                last_error = error
        raise last_error

    return ask
