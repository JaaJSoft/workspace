import json
import logging
import re
import time

from django.conf import settings

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r'<think>[\s\S]*?</think>\s*', re.IGNORECASE)
_RAW_TOOL_CALL_RE = re.compile(r'</?tool_call>', re.IGNORECASE)
# Matches timestamp prefixes leaked by the LLM, with or without brackets:
# "[2026-04-10 20:07] ..." or "2026-04-10 20:07 ..."
_TIMESTAMP_PREFIX_RE = re.compile(r'^\[?\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\]?\s*')


def clean_llm_content(content: str) -> str:
    """Strip artifacts that LLMs sometimes leak into their replies."""
    content = _RAW_TOOL_CALL_RE.sub('', content)
    content = _TIMESTAMP_PREFIX_RE.sub('', content)
    return content.strip()


def serialize_response(result):
    """Serialize an call_llm result dict for storage."""
    tc = result.get('tool_calls')
    return {
        'content': result.get('content', ''),
        'tool_calls': [
            {'id': c.id, 'name': c.function.name, 'arguments': c.function.arguments}
            for c in tc
        ] if tc else None,
        'model': result.get('model', ''),
        'prompt_tokens': result.get('prompt_tokens'),
        'completion_tokens': result.get('completion_tokens'),
    }


def sanitize_messages_for_storage(messages):
    """Strip large base64 image data and truncate huge text from messages."""
    sanitized = []
    for msg in messages:
        content = msg.get('content')
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and (
                    part.get('type') == 'image_url' or 'image_url' in part
                ):
                    parts.append({'type': 'image_url', 'image_url': '[stripped]'})
                else:
                    parts.append(part)
            sanitized.append({**msg, 'content': parts})
        elif isinstance(content, str) and len(content) > 50_000:
            sanitized.append({**msg, 'content': content[:50_000] + '… [truncated]'})
        else:
            sanitized.append(msg)
    return sanitized


def truncate_tool_result(text, max_len=2000):
    """Truncate tool result strings for storage, stripping image data."""
    if not text:
        return text
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get('type') == 'image':
            return json.dumps({'type': 'image', 'data': '[stripped]'})
    except (json.JSONDecodeError, TypeError):
        # Not an image payload (most tool results are plain text JSON or raw
        # strings) - fall through to plain length-based truncation.
        pass
    if len(text) > max_len:
        return text[:max_len] + '… [truncated]'
    return text


def build_tool_content(tool_result: str):
    """Convert a tool result string into API content, handling image payloads."""
    try:
        parsed = json.loads(tool_result)
        if isinstance(parsed, dict) and parsed.get('type') == 'image':
            mime = parsed.get('mime_type', 'image/webp')
            data = parsed['data']
            return [
                {'type': 'text', 'text': 'Here is the image:'},
                {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{data}'}},
            ]
    except (json.JSONDecodeError, KeyError):
        # Non-image tool result (the common case) - return the raw text.
        pass
    return tool_result


def _strip_thinking(content: str) -> str:
    """Remove <think>...</think> blocks from model output."""
    return _THINK_RE.sub('', content).strip()


def extract_text_tool_calls(content: str):
    """Parse tool calls that a model emitted as plain text instead of structured output.

    Handles two formats:
    - ``{"tool": "name", "prompt": "...", ...}`` (shorthand some models use)
    - ``{"name": "name", "arguments": {...}}`` (OpenAI-like)

    Returns a list of (name, arguments_json) tuples and the remaining text,
    or (None, content) if nothing was found.
    """
    cleaned = _RAW_TOOL_CALL_RE.sub('', content).strip()

    calls = []
    remaining = cleaned

    for match in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', cleaned):
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue

        match parsed:
            case {'tool': name, **rest}:
                calls.append((name, json.dumps(rest)))
                remaining = remaining.replace(match.group(), '').strip()
            case {'name': name, 'arguments': args}:
                args_json = args if isinstance(args, str) else json.dumps(args)
                calls.append((name, args_json))
                remaining = remaining.replace(match.group(), '').strip()

    if calls:
        logger.info('Parsed %d tool call(s) from text content', len(calls))
        return calls, remaining
    return None, content


def call_llm(messages: list[dict], model: str | None = None, max_tokens: int | None = None, tools: list | None = None) -> dict:
    """Call an LLM via OpenAI SDK and return a dict with content and usage info."""
    from workspace.ai.client import get_ai_client
    from workspace.ai.metrics import AI_REQUEST_DURATION, AI_TOKENS

    client = get_ai_client()
    if not client:
        raise RuntimeError('AI is not configured (AI_API_KEY missing)')

    effective_model = model or settings.AI_MODEL
    kwargs = {
        'model': effective_model,
        'messages': messages,
        'max_tokens': max_tokens or settings.AI_MAX_TOKENS,
    }
    if tools:
        kwargs['tools'] = tools
        kwargs['tool_choice'] = 'auto'

    started = time.monotonic()
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception:
        AI_REQUEST_DURATION.labels(
            model=effective_model, status='error',
        ).observe(time.monotonic() - started)
        raise
    AI_REQUEST_DURATION.labels(
        model=response.model or effective_model, status='ok',
    ).observe(time.monotonic() - started)

    if response.usage:
        if response.usage.prompt_tokens:
            AI_TOKENS.labels(
                model=response.model or effective_model, kind='prompt',
            ).inc(response.usage.prompt_tokens)
        if response.usage.completion_tokens:
            AI_TOKENS.labels(
                model=response.model or effective_model, kind='completion',
            ).inc(response.usage.completion_tokens)

    choice = response.choices[0]
    # Apply both strip and clean here so downstream consumers (summaries, mail
    # composer, titles, ...) see normalized text regardless of which path they
    # took.
    content = clean_llm_content(_strip_thinking(choice.message.content or ''))
    return {
        'content': content,
        'tool_calls': choice.message.tool_calls,
        'message': choice.message,
        'model': response.model,
        'prompt_tokens': response.usage.prompt_tokens if response.usage else None,
        'completion_tokens': response.usage.completion_tokens if response.usage else None,
    }
