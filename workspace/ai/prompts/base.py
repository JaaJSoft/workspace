from django.utils import timezone
from django.utils.translation import get_language_info
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    SystemPromptPart,
    UserPromptPart,
)


def prompt(system: str, user) -> list[ModelMessage]:
    """A system prompt and one user message, as the conversation a model is sent."""
    return [ModelRequest(parts=[SystemPromptPart(system), UserPromptPart(user)])]


def send(messages: list[ModelMessage], *parts) -> None:
    """Add *parts* to what the model is sent before its next turn.

    Everything a model reads between two of its own turns is one request,
    whoever wrote it: a system line, the user, the result of a tool.
    """
    if messages and isinstance(messages[-1], ModelRequest):
        messages[-1].parts.extend(parts)
    else:
        messages.append(ModelRequest(parts=list(parts)))


def truncate_text(text: str, max_chars: int = 8000) -> str:
    """Truncate text to fit within a character limit."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... truncated ...]"


def sanitize_prompt_line(value: str, max_len: int) -> str:
    """Flatten a user-controlled value into a single line safe to embed in a prompt.

    `str.isprintable()` is False for newlines, tabs and control characters and
    True for a plain space, so the result cannot break out of the line it sits
    on to forge an extra bullet or section header the model would read as an
    instruction.
    """
    return "".join(c for c in (value or "") if c.isprintable()).strip()[:max_len]


def reply_language(user) -> str:
    """English name of *user*'s interface language, for a prompt to answer in.

    Named in English whatever the language: the prompts are English, and a
    model reads "French" more reliably than "fr" or "Français".
    """
    from workspace.users.services.settings import get_user_language

    return get_language_info(get_user_language(user))["name"]


def build_context_block(user=None) -> str:
    """Build a context block with current date/time info for prompts.

    If *user* is provided, the time is displayed in the user's configured
    timezone instead of the server default (UTC).
    """
    if user:
        from workspace.users.services.settings import get_user_timezone

        user_tz = get_user_timezone(user)
        now = timezone.now().astimezone(user_tz)
        tz_label = str(user_tz)
    else:
        now = timezone.localtime()
        tz_label = str(now.tzinfo)
    return (
        f"Current date: {now.strftime('%A, %B %d, %Y')}\n"
        f"Current time: {now.strftime('%H:%M')} ({tz_label})"
    )
