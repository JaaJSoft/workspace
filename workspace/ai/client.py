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


def get_image_client() -> OpenAI | None:
    """Return an OpenAI client configured for image generation.

    Uses AI_IMAGE_BASE_URL if set, otherwise falls back to AI_BASE_URL.
    """
    if not settings.AI_API_KEY:
        return None
    return OpenAI(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_IMAGE_BASE_URL or settings.AI_BASE_URL,
    )


def is_ai_enabled() -> bool:
    """Check whether AI features are available."""
    return bool(settings.AI_API_KEY)
