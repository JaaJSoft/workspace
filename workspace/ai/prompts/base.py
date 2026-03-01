from django.utils import timezone


def truncate_text(text: str, max_chars: int = 8000) -> str:
    """Truncate text to fit within a character limit."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + '\n\n[... truncated ...]'


def build_context_block() -> str:
    """Build a context block with current date/time info for prompts."""
    now = timezone.localtime()
    return (
        f"Current date: {now.strftime('%A, %B %d, %Y')}\n"
        f"Current time: {now.strftime('%H:%M')} ({now.tzinfo})"
    )
