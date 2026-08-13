import json
import logging
import re
import time

from django.conf import settings
from pydantic import BaseModel, ValidationError

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

# Backends spell their inline reasoning tag differently. The backreference keeps
# an opening tag from being closed by a different one, and the body refuses to
# cross a further opening tag so a mismatched closer cannot merge two blocks -
# and with them the answer in between - into a single one.
_THINK_TAGS = "think|thinking|thought|thoughts|reasoning"
_THINK_RE = re.compile(
    rf"<({_THINK_TAGS})>((?:(?!<(?:{_THINK_TAGS})>)[\s\S])*?)</\1>\s*",
    re.IGNORECASE,
)
_RAW_TOOL_CALL_RE = re.compile(r"</?tool_call>", re.IGNORECASE)
# Matches timestamp prefixes leaked by the LLM, with or without brackets:
# "[2026-04-10 20:07] ..." or "2026-04-10 20:07 ..."
_TIMESTAMP_PREFIX_RE = re.compile(r"^\[?\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\]?\s*")
# Image stand-ins injected by conversation_history; the model imitates them.
# Stripped anywhere (never legitimate output); the leading newline run is put back
# so a standalone marker leaves no blank line, and the `!` lookbehind spares a
# markdown image whose alt text starts with "image:".
_IMAGE_MARKER_RE = re.compile(
    r"(\n*)(?<!!)\[(?:Images sent by the assistant in the message above"
    r"|image:[^\n]*?)\][ \t]*\n*",
    re.IGNORECASE,
)
# Fallback for backends that ignore response_format and wrap their JSON in
# markdown fences anyway.
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.DOTALL)


def clean_llm_content(content: str) -> str:
    """Strip artifacts that LLMs sometimes leak into their replies."""
    content = _RAW_TOOL_CALL_RE.sub("", content)
    # Markers first: _TIMESTAMP_PREFIX_RE is anchored, so a timestamp sitting
    # behind one is only reachable once the marker is gone.
    content = _IMAGE_MARKER_RE.sub(r"\1", content)
    content = _TIMESTAMP_PREFIX_RE.sub("", content)
    return content.strip()


def serialize_response(result):
    """Serialize an call_llm result dict for storage."""
    tc = result.get("tool_calls")
    return {
        "content": result.get("content", ""),
        "thinking": result.get("thinking", ""),
        "tool_calls": [
            {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
            for c in tc
        ]
        if tc
        else None,
        "model": result.get("model", ""),
        "prompt_tokens": result.get("prompt_tokens"),
        "completion_tokens": result.get("completion_tokens"),
    }


def sanitize_messages_for_storage(messages):
    """Strip large base64 image data and truncate huge text from messages."""
    sanitized = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and (
                    part.get("type") == "image_url" or "image_url" in part
                ):
                    parts.append({"type": "image_url", "image_url": "[stripped]"})
                else:
                    parts.append(part)
            sanitized.append({**msg, "content": parts})
        elif isinstance(content, str) and len(content) > 50_000:
            sanitized.append({**msg, "content": content[:50_000] + "… [truncated]"})
        else:
            sanitized.append(msg)
    return sanitized


def truncate_tool_result(text, max_len=2000):
    """Truncate tool result strings for storage, stripping image data."""
    if not text:
        return text
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get("type") == "image":
            stripped = {"type": "image", "data": "[stripped]"}
            if parsed.get("text"):
                stripped["text"] = parsed["text"]
            return json.dumps(stripped)
    except json.JSONDecodeError, TypeError:
        # Not an image payload (most tool results are plain text JSON or raw
        # strings) - fall through to plain length-based truncation.
        pass
    if len(text) > max_len:
        return text[:max_len] + "… [truncated]"
    return text


def build_tool_content(tool_result: str):
    """Convert a tool result string into API content, handling image payloads."""
    try:
        parsed = json.loads(tool_result)
        if isinstance(parsed, dict) and parsed.get("type") == "image":
            mime = parsed.get("mime_type", "image/webp")
            data = parsed["data"]
            return [
                {"type": "text", "text": parsed.get("text") or "Here is the image:"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data}"},
                },
            ]
    except json.JSONDecodeError, KeyError:
        # Non-image tool result (the common case) - return the raw text.
        pass
    return tool_result


def _extract_thinking(content: str) -> tuple[str, str]:
    """Split inline reasoning blocks (<think>, <thought>, ...) out of model output.

    Returns (thinking, cleaned). Multiple blocks join with a blank line.
    An unclosed tag matches nothing: it stays in the content and captures no
    thinking.
    """
    blocks = [m.group(2).strip() for m in _THINK_RE.finditer(content)]
    thinking = "\n\n".join(b for b in blocks if b)
    cleaned = _THINK_RE.sub("", content).strip()
    return thinking, cleaned


def extract_text_tool_calls(content: str):
    """Parse tool calls that a model emitted as plain text instead of structured output.

    Handles two formats:
    - ``{"tool": "name", "prompt": "...", ...}`` (shorthand some models use)
    - ``{"name": "name", "arguments": {...}}`` (OpenAI-like)

    Returns a list of (name, arguments_json) tuples and the remaining text,
    or (None, content) if nothing was found.
    """
    cleaned = _RAW_TOOL_CALL_RE.sub("", content).strip()

    calls = []
    remaining = cleaned

    for match in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned):
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue

        match parsed:
            case {"tool": name, **rest}:
                calls.append((name, json.dumps(rest)))
                remaining = remaining.replace(match.group(), "").strip()
            case {"name": name, "arguments": args}:
                args_json = args if isinstance(args, str) else json.dumps(args)
                calls.append((name, args_json))
                remaining = remaining.replace(match.group(), "").strip()

    if calls:
        logger.info("Parsed %d tool call(s) from text content", len(calls))
        return calls, remaining
    return None, content


def call_llm(
    messages: list[dict],
    model: str | None = None,
    max_tokens: int | None = None,
    tools: list | None = None,
    response_format: dict | None = None,
) -> dict:
    """Call an LLM via OpenAI SDK and return a dict with content and usage info."""
    from workspace.ai.client import get_ai_client
    from workspace.ai.metrics import AI_REQUEST_DURATION, AI_TOKENS

    client = get_ai_client()
    if not client:
        raise RuntimeError("AI is not configured (AI_API_KEY missing)")

    effective_model = model or settings.AI_MODEL
    kwargs = {
        "model": effective_model,
        "messages": messages,
        "max_tokens": max_tokens or settings.AI_MAX_TOKENS,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if response_format:
        kwargs["response_format"] = response_format

    started = time.monotonic()
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception:
        AI_REQUEST_DURATION.labels(
            model=effective_model,
            status="error",
        ).observe(time.monotonic() - started)
        raise
    AI_REQUEST_DURATION.labels(
        model=response.model or effective_model,
        status="ok",
    ).observe(time.monotonic() - started)

    if response.usage:
        if response.usage.prompt_tokens:
            AI_TOKENS.labels(
                model=response.model or effective_model,
                kind="prompt",
            ).inc(response.usage.prompt_tokens)
        if response.usage.completion_tokens:
            AI_TOKENS.labels(
                model=response.model or effective_model,
                kind="completion",
            ).inc(response.usage.completion_tokens)

    choice = response.choices[0]
    thinking, content = _extract_thinking(choice.message.content or "")
    # Some backends (vLLM/DeepSeek: reasoning_content, OpenRouter: reasoning)
    # return reasoning as a separate field instead of <think> tags. First
    # non-blank field wins, so a whitespace-only reasoning_content still
    # falls back to reasoning.
    native = next(
        (
            v.strip()
            for v in (
                getattr(choice.message, "reasoning_content", None),
                getattr(choice.message, "reasoning", None),
            )
            if isinstance(v, str) and v.strip()
        ),
        "",
    )
    if native:
        thinking = native
    # Apply both strip and clean here so downstream consumers (summaries, mail
    # composer, titles, ...) see normalized text regardless of which path they
    # took.
    content = clean_llm_content(content)
    return {
        "content": content,
        "thinking": thinking,
        "tool_calls": choice.message.tool_calls,
        "message": choice.message,
        "model": response.model,
        "prompt_tokens": response.usage.prompt_tokens if response.usage else None,
        "completion_tokens": response.usage.completion_tokens
        if response.usage
        else None,
    }


def call_llm_structured(
    messages: list[dict],
    schema: type[BaseModel],
    model: str | None = None,
    max_tokens: int | None = None,
) -> tuple[BaseModel | None, dict]:
    """Call the LLM with output constrained to *schema* and validate the reply.

    The schema is sent as a ``json_schema`` response_format so backends with
    constrained decoding (OpenAI, vLLM, Ollama, LM Studio) guarantee the shape
    at generation time. The reply is validated with Pydantic regardless: a
    backend may silently ignore the constraint, which is also why stray
    markdown fences are still stripped before parsing.

    *schema* must describe a top-level JSON object, not an array - the
    json_schema response format requires it. Wrap lists in an envelope model.

    Returns ``(instance, result)`` where *instance* is ``None`` when the reply
    does not validate; *result* is the raw :func:`call_llm` dict so callers can
    still record model and token usage.
    """
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "schema": schema.model_json_schema(),
        },
    }
    result = call_llm(
        messages,
        model=model,
        max_tokens=max_tokens,
        response_format=response_format,
    )
    raw = _FENCE_RE.sub("", (result.get("content") or "").strip())
    try:
        return schema.model_validate_json(raw), result
    except ValidationError as e:
        logger.warning(
            "LLM output failed %s validation: %s",
            schema.__name__,
            scrub(str(e)[:500]),
        )
        return None, result
