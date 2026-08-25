from django.conf import settings
from openai import OpenAI


def get_ai_client() -> OpenAI | None:
    """Return a configured OpenAI client, or None if AI is not configured."""
    if not settings.AI_API_KEY:
        return None
    return OpenAI(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL,
        timeout=settings.AI_TIMEOUT,
        max_retries=settings.AI_MAX_RETRIES,
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
        timeout=settings.AI_TIMEOUT,
        max_retries=settings.AI_MAX_RETRIES,
    )


def get_speech_client() -> OpenAI | None:
    """Return an OpenAI client configured for speech synthesis.

    Unlike the others, this one is built without an API key when none is set:
    the speech server has no authentication of its own, and reaching it
    directly on the internal network is a supported deployment. The SDK
    refuses to construct without a key, hence the placeholder.
    """
    base_url = settings.AI_TTS_BASE_URL or settings.AI_BASE_URL
    if not base_url:
        return None
    return OpenAI(
        api_key=settings.AI_API_KEY or "unused",
        base_url=base_url,
        timeout=settings.AI_TTS_TIMEOUT,
        max_retries=0,  # ai_synthesize_speech owns the retry policy
    )


def is_ai_enabled() -> bool:
    """Check whether AI features are available."""
    return bool(settings.AI_API_KEY)
