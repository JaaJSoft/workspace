from django.conf import settings
from openai import OpenAI


def get_ai_client() -> OpenAI | None:
    """Return a configured OpenAI client, or None if AI is not configured."""
    if not settings.AI_API_KEY:
        return None
    return OpenAI(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL,
    )


def is_ai_enabled() -> bool:
    """Check whether AI features are available."""
    return bool(settings.AI_API_KEY)
