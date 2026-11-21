"""The configured backend, as Pydantic AI models, and one-shot prompts on it.

Everything that talks to a text model goes through :func:`get_model`:
the bots' agent, and the single requests of titles, summaries, mail and
the editor, which :func:`run_prompt` sends. How a request is written for
a backend is the model class's business; what is said here is which kind
of backend the settings describe.
"""

import time
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

import pydantic_ai
from django.conf import settings
from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    TextPart,
    ThinkingPart,
)
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from workspace.ai.metrics import AI_REQUEST_DURATION, AI_TOKENS
from workspace.ai.services.reply_text import (
    clean_llm_content,
    split_inline_thinking,
    truncate_middle,
)

# Printed into the worker's log on the first run of a process otherwise.
pydantic_ai.BANNER_ENABLED = False

# Characters of one text kept in a stored conversation.
_STORED_TEXT_MAX_CHARS = 50_000


def _is_openai(base_url: str) -> bool:
    """Whether *base_url* is OpenAI's own API, hosted by it or by Azure."""
    host = urlsplit(base_url).hostname or ""
    return host == "api.openai.com" or host.endswith(".openai.azure.com")


def _compatible_profile(model_name: str):
    """What an endpoint that only speaks OpenAI's protocol is taken to do.

    The same whatever its model is called. Left to itself the library reads
    a vendor off a "vendor/model" name and takes that vendor's own API as
    the limit: no JSON schema output, no system message past the first.
    Behind LM Studio, vLLM or OpenRouter the name says nothing of the kind.
    """
    from pydantic_ai.profiles import merge_profile
    from pydantic_ai.profiles.openai import OpenAIModelProfile, openai_model_profile

    return merge_profile(
        openai_model_profile(model_name),
        OpenAIModelProfile(
            supports_json_schema_output=True,
            supports_json_object_output=True,
            supports_inline_system_prompts=True,
            # Ollama ignores `max_completion_tokens` without a word and
            # answers uncapped; every such service reads `max_tokens`.
            openai_chat_supports_max_completion_tokens=False,
            # Handed its reasoning back, gpt-oss writes its next tool
            # call into its reasoning, where nothing runs it.
            openai_chat_send_back_thinking_parts=False,
        ),
    )


def get_model(name: str | None = None) -> Model:
    """The model *name*, or the default one, on the configured backend."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider

    if not settings.AI_API_KEY:
        raise RuntimeError("AI is not configured (AI_API_KEY missing)")
    base_url = settings.AI_BASE_URL
    if not base_url or _is_openai(base_url):
        provider = OpenAIProvider(base_url=base_url, api_key=settings.AI_API_KEY)
    else:
        provider = OpenAICompatibleProvider(
            "openai-compatible",
            base_url=base_url,
            api_key=settings.AI_API_KEY,
            profile=_compatible_profile,
        )
    return OpenAIChatModel(name or settings.AI_MODEL, provider=provider)


def model_settings(max_tokens: int | None = None) -> ModelSettings:
    return ModelSettings(
        max_tokens=max_tokens or settings.AI_MAX_TOKENS,
        timeout=settings.AI_TIMEOUT,
        max_retries=settings.AI_MAX_RETRIES,
    )


class ReplyHygiene(AbstractCapability):
    """Cleans every reply before anything reads it.

    Reasoning a model wrote between inline tags becomes a thinking part,
    like reasoning a backend kept apart, and the artifacts a model imitates
    from its own history are cut from the text.
    """

    async def after_model_request(self, ctx, *, request_context, response):
        parts = []
        for part in response.parts:
            if not isinstance(part, TextPart):
                parts.append(part)
                continue
            thinking, text = split_inline_thinking(part.content)
            if thinking:
                parts.append(ThinkingPart(thinking))
            if text := clean_llm_content(text):
                parts.append(replace(part, content=text))
        return replace(response, parts=parts)


@dataclass
class RequestMetrics(AbstractCapability):
    """Times every model request and counts its tokens for Prometheus.

    ``seconds`` adds up the requests of a run and ``last_seconds`` is the
    latest one: a run reads there what it cost in model time.
    """

    seconds: float | None = None
    last_seconds: float | None = None

    async def wrap_model_request(self, ctx, *, request_context, handler):
        started = time.monotonic()
        try:
            response = await handler(request_context)
        except Exception:
            AI_REQUEST_DURATION.labels(
                model=request_context.model.model_name, status="error"
            ).observe(time.monotonic() - started)
            raise
        self.last_seconds = time.monotonic() - started
        self.seconds = (self.seconds or 0) + self.last_seconds
        model = response.model_name or request_context.model.model_name
        AI_REQUEST_DURATION.labels(model=model, status="ok").observe(self.last_seconds)
        if response.usage.input_tokens:
            AI_TOKENS.labels(model=model, kind="prompt").inc(
                response.usage.input_tokens
            )
        if response.usage.output_tokens:
            AI_TOKENS.labels(model=model, kind="completion").inc(
                response.usage.output_tokens
            )
        return response


def run_prompt(
    messages: list[ModelMessage],
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    output_type=str,
):
    """Send *messages* once and return the run's result.

    *output_type* is what Pydantic AI should make of the reply: text by
    default, or a schema (``NativeOutput(Model)``) the reply is constrained
    to and validated against. A reply that cannot be read as it, or that
    the backend cut at the token cap, raises.
    """
    agent = Agent(
        get_model(model),
        output_type=output_type,
        model_settings=model_settings(max_tokens),
        capabilities=[ReplyHygiene(), RequestMetrics()],
    )
    return agent.run_sync(message_history=messages)


def usage_fields(response: ModelResponse, usage) -> dict:
    """What a run cost, as the AITask columns that record it.

    A backend that reports no usage leaves the counts unknown, not at zero.
    """
    return {
        "model_used": response.model_name or "",
        "prompt_tokens": usage.input_tokens or None,
        "completion_tokens": usage.output_tokens or None,
    }


def _stored(value):
    if isinstance(value, dict):
        if value.get("kind") == "binary":
            return {**value, "data": "[stripped]"}
        if value.get("part_kind") == "tool-return" and isinstance(
            value.get("content"), str
        ):
            cut = truncate_middle(
                value["content"], settings.AI_TOOL_RESULT_TASK_MAX_CHARS
            )
            return {**value, "content": cut}
        return {key: _stored(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_stored(item) for item in value]
    if isinstance(value, str) and len(value) > _STORED_TEXT_MAX_CHARS:
        return value[:_STORED_TEXT_MAX_CHARS] + "… [truncated]"
    return value


def messages_for_storage(messages: list[ModelMessage]) -> list:
    """A conversation as ``AITask.raw_messages`` keeps it, for a human to read.

    Pydantic AI's own serialization, minus what makes a row heavy: the bytes
    of images, the tail of very long texts, and all of a tool result past
    ``AI_TOOL_RESULT_TASK_MAX_CHARS``.
    """
    return _stored(ModelMessagesTypeAdapter.dump_python(messages, mode="json"))
