from django.utils import timezone


def truncate_text(text: str, max_chars: int = 8000) -> str:
    """Truncate text to fit within a character limit."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + '\n\n[... truncated ...]'


def build_context_block(user=None) -> str:
    """Build a context block with current date/time info for prompts.

    If *user* is provided, the time is displayed in the user's configured
    timezone instead of the server default (UTC).
    """
    if user:
        from workspace.users.settings_service import get_user_timezone
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
