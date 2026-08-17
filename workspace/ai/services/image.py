"""Standalone AI image generation/editing service used by chat tools and REST endpoints."""

import base64
import binascii
import io
import logging
import time

from django.conf import settings
from openai import APIStatusError

from workspace.common.logging import scrub

from ..client import get_image_client

logger = logging.getLogger(__name__)

VALID_SIZES = ("1024x1024", "1792x1024", "1024x1792")
DEFAULT_SIZE = "1024x1024"

# Sub-500 statuses a later call can still clear: request timeout, conflict,
# too early, rate limit. Anything else below 500 is a rejection that would
# repeat identically (bad prompt, bad key, unknown model).
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})


class ImageGenerationError(RuntimeError):
    """The image backend did not return an image.

    *attempts* is how many calls were made before giving up: a rejected
    prompt stops at one, a flaky backend burns the whole budget.

    *rejected* says the backend passed a verdict on the request itself
    rather than falling over. It is the difference between the two pieces
    of advice worth giving a model: rewrite the prompt, or stop trying and
    tell the user the service is down.
    """

    def __init__(self, message, attempts=1, rejected=False):
        super().__init__(message)
        self.attempts = attempts
        self.rejected = rejected


def ai_generate_image(prompt: str, size: str = DEFAULT_SIZE) -> bytes:
    """Generate an image from a text description.

    Args:
        prompt: Text description of the image to create.
        size: Output size. Must be one of VALID_SIZES; defaults to
              '1024x1024' if an invalid value is given.

    Returns:
        Raw bytes of the generated image.

    Raises:
        ValueError: If *prompt* is empty or AI is not configured.
        ImageGenerationError: If every attempt failed.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt is required")

    client = get_image_client()
    if not client:
        raise ValueError("AI is not configured")

    size = normalize_size(size)

    logger.info(
        "Starting image generation: model=%s size=%s prompt=%.80s",
        settings.AI_IMAGE_MODEL,
        size,
        scrub(prompt),
    )

    def attempt():
        response = client.images.generate(
            model=settings.AI_IMAGE_MODEL,
            prompt=prompt,
            size=size,
            n=1,
            response_format="b64_json",
        )
        return _decode_first_image(response)

    image_data = _run_with_retry(attempt, "generate", prompt)

    logger.info(
        "Image generated: model=%s size=%s bytes=%d prompt=%.80s",
        settings.AI_IMAGE_MODEL,
        size,
        len(image_data),
        scrub(prompt),
    )
    return image_data


def ai_edit_image(source_data: bytes, prompt: str, size: str = DEFAULT_SIZE) -> bytes:
    """Edit an image using the configured AI backend.

    Args:
        source_data: Raw bytes of the source image.
        prompt: Text instruction describing the desired edit.
        size: Output size. Must be one of VALID_SIZES; defaults to
              '1024x1024' if an invalid value is given.

    Returns:
        Raw bytes of the edited image.

    Raises:
        ValueError: If *prompt* is empty or AI is not configured.
        ImageGenerationError: If every attempt failed on both the OpenAI
            and Ollama backends.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt is required")

    client = get_image_client()
    if not client:
        raise ValueError("AI is not configured")

    size = normalize_size(size)

    logger.info(
        "Starting image edit: model=%s size=%s prompt=%.80s",
        settings.AI_IMAGE_MODEL,
        size,
        scrub(prompt),
    )

    def attempt():
        # Try OpenAI-compatible endpoint first, fall back to Ollama native API
        try:
            image_file = io.BytesIO(source_data)
            image_file.name = "image.png"
            image_data = _edit_via_openai(client, image_file, prompt, size)
            logger.info(
                "Image edited via OpenAI endpoint: model=%s bytes=%d",
                settings.AI_IMAGE_MODEL,
                len(image_data),
            )
        except Exception as openai_err:
            logger.info(
                "OpenAI images.edit failed (%s), falling back to Ollama native API",
                openai_err,
            )
            image_data = _edit_via_ollama(source_data, prompt)
            logger.info(
                "Image edited via Ollama native API: model=%s bytes=%d",
                settings.AI_IMAGE_MODEL,
                len(image_data),
            )
        return image_data

    return _run_with_retry(attempt, "edit", prompt)


def normalize_size(size):
    return size if size in VALID_SIZES else DEFAULT_SIZE


def _decode_first_image(response) -> bytes:
    """Extract the first image of an OpenAI-shaped response, or b'' if unusable."""
    data = getattr(response, "data", None) or []
    b64 = data[0].b64_json if data else None
    if not b64:
        return b""
    try:
        return base64.b64decode(b64)
    except binascii.Error, ValueError:
        return b""


def _is_retryable(exc: BaseException) -> bool:
    """Whether an identical call still has a chance of succeeding.

    The cause/context chain is walked because the edit path reports a
    backend rejection wrapped in the error of its fallback backend.
    """
    for _ in range(5):
        if exc is None:
            break
        if isinstance(exc, APIStatusError):
            status = getattr(exc, "status_code", None) or 0
            return status in RETRYABLE_STATUSES or status >= 500
        exc = exc.__cause__ or exc.__context__
    return True


def _run_with_retry(operation, op: str, prompt: str) -> bytes:
    """Call *operation* until it returns image bytes, retrying transient failures.

    Image backends fail shallowly and often: a rate limit, an upstream 5xx,
    or a 200 carrying an empty payload. A single hiccup used to reach the
    model as a tool error, and a model asked for several images answers with
    the ones that worked instead of calling the tool again — so the retry
    happens here, before the failure is ever visible to it.

    Raises:
        ImageGenerationError: carrying the number of attempts made.
    """
    from ..metrics import AI_IMAGE_REQUESTS

    attempts = max(1, settings.AI_IMAGE_MAX_ATTEMPTS)
    delay = max(0.0, settings.AI_IMAGE_RETRY_DELAY)

    for attempt in range(1, attempts + 1):
        try:
            image_data = operation()
        except Exception as exc:
            failure = exc
        else:
            if image_data:
                AI_IMAGE_REQUESTS.labels(
                    model=settings.AI_IMAGE_MODEL,
                    op=op,
                    status="ok",
                ).inc()
                return image_data
            failure = ImageGenerationError("the image model returned no image")

        AI_IMAGE_REQUESTS.labels(
            model=settings.AI_IMAGE_MODEL,
            op=op,
            status="error",
        ).inc()

        rejected = not _is_retryable(failure)
        if attempt >= attempts or rejected:
            break

        logger.warning(
            "Image %s attempt %d/%d failed (%s), retrying in %.1fs: prompt=%.80s",
            op,
            attempt,
            attempts,
            failure,
            delay,
            scrub(prompt),
        )
        if delay:
            time.sleep(delay)
        delay *= 2

    logger.error(
        "Image %s failed after %d attempt(s): model=%s rejected=%s "
        "error=%s prompt=%.80s",
        op,
        attempt,
        settings.AI_IMAGE_MODEL,
        rejected,
        failure,
        scrub(prompt),
    )
    raise ImageGenerationError(
        str(failure), attempts=attempt, rejected=rejected
    ) from failure


def _edit_via_openai(client, image_file, prompt, size):
    """Try editing via the OpenAI-compatible /v1/images/edits endpoint."""
    response = client.images.edit(
        model=settings.AI_IMAGE_MODEL,
        image=image_file,
        prompt=prompt,
        size=size,
        n=1,
        response_format="b64_json",
    )
    image_data = _decode_first_image(response)
    if not image_data:
        raise RuntimeError("OpenAI images.edit returned no image")
    return image_data


def _edit_via_ollama(source_data, prompt):
    """Fallback: use Ollama native /api/generate with images param (img2img)."""
    import httpx2

    base_url = (settings.AI_IMAGE_BASE_URL or settings.AI_BASE_URL or "").rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    resp = httpx2.post(
        f"{base_url}/api/generate",
        json={
            "model": settings.AI_IMAGE_MODEL,
            "prompt": prompt,
            "images": [base64.b64encode(source_data).decode()],
            "stream": False,
        },
        timeout=settings.AI_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    # Ollama returns 'image' (singular) for img2img
    result_b64 = data.get("image") or ""
    if not result_b64:
        raise RuntimeError(
            f"no image returned from Ollama — response keys: {list(data.keys())}"
        )
    return base64.b64decode(result_b64)
