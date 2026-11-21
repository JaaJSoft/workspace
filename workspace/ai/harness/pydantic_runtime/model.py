"""The configured backend, as a Pydantic AI model.

Which parameters a backend takes, and how it names them, is the model
class's business from here on: nothing in this module spells a request.
"""

from contextlib import asynccontextmanager

from django.conf import settings
from pydantic_ai.settings import ModelSettings


def model_settings() -> ModelSettings:
    return ModelSettings(max_tokens=settings.AI_MAX_TOKENS, timeout=settings.AI_TIMEOUT)


@asynccontextmanager
async def open_model(name: str | None):
    """The model *name* on the configured backend, for the time of one run.

    The client is opened and closed with the run: a run has an event loop
    of its own, and a connection kept past it belongs to a loop that is
    gone.
    """
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.profiles.openai import OpenAIModelProfile
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider

    if not settings.AI_API_KEY:
        raise RuntimeError("AI is not configured (AI_API_KEY missing)")
    client = AsyncOpenAI(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL,
        timeout=settings.AI_TIMEOUT,
        max_retries=settings.AI_MAX_RETRIES,
    )
    # Anything but OpenAI gets the reply cap as `max_tokens`: Ollama ignores
    # `max_completion_tokens` without a word and answers uncapped.
    if settings.AI_BASE_URL:
        provider = OpenAICompatibleProvider(
            "openai-compatible",
            openai_client=client,
            profile=OpenAIModelProfile(
                openai_chat_supports_max_completion_tokens=False
            ),
        )
    else:
        provider = OpenAIProvider(openai_client=client)
    try:
        yield OpenAIChatModel(name or settings.AI_MODEL, provider=provider)
    finally:
        await client.close()
