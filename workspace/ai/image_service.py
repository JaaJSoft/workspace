"""Standalone AI image editing service used by chat tools and REST endpoints."""
import base64
import io
import logging

from django.conf import settings

from .client import get_image_client

logger = logging.getLogger(__name__)

VALID_SIZES = ('1024x1024', '1792x1024', '1024x1792')


def ai_edit_image(source_data: bytes, prompt: str, size: str = '1024x1024') -> bytes:
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
        RuntimeError: If both the OpenAI and Ollama backends fail.
    """
    if not prompt or not prompt.strip():
        raise ValueError('prompt is required')

    client = get_image_client()
    if not client:
        raise ValueError('AI is not configured')

    if size not in VALID_SIZES:
        size = '1024x1024'

    logger.info(
        'Starting image edit: model=%s size=%s prompt=%.80s',
        settings.AI_IMAGE_MODEL, size, prompt,
    )

    # Try OpenAI-compatible endpoint first, fall back to Ollama native API
    try:
        image_file = io.BytesIO(source_data)
        image_file.name = 'image.png'
        image_data = _edit_via_openai(client, image_file, prompt, size)
        logger.info('Image edited via OpenAI endpoint: model=%s bytes=%d', settings.AI_IMAGE_MODEL, len(image_data))
    except Exception as openai_err:
        logger.info('OpenAI images.edit failed (%s), falling back to Ollama native API', openai_err)
        try:
            image_data = _edit_via_ollama(source_data, prompt)
            logger.info('Image edited via Ollama native API: model=%s bytes=%d', settings.AI_IMAGE_MODEL, len(image_data))
        except Exception as ollama_err:
            logger.exception('Image edit failed on both OpenAI and Ollama backends')
            raise RuntimeError(f'image edit failed — {ollama_err}') from ollama_err

    return image_data


def _edit_via_openai(client, image_file, prompt, size):
    """Try editing via the OpenAI-compatible /v1/images/edits endpoint."""
    response = client.images.edit(
        model=settings.AI_IMAGE_MODEL,
        image=image_file,
        prompt=prompt,
        size=size,
        n=1,
        response_format='b64_json',
    )
    return base64.b64decode(response.data[0].b64_json)


def _edit_via_ollama(source_data, prompt):
    """Fallback: use Ollama native /api/generate with images param (img2img)."""
    import httpx
    base_url = (settings.AI_IMAGE_BASE_URL or settings.AI_BASE_URL or '').rstrip('/')
    if base_url.endswith('/v1'):
        base_url = base_url[:-3]
    resp = httpx.post(
        f'{base_url}/api/generate',
        json={
            'model': settings.AI_IMAGE_MODEL,
            'prompt': prompt,
            'images': [base64.b64encode(source_data).decode()],
            'stream': False,
        },
        timeout=settings.AI_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    # Ollama returns 'image' (singular) for img2img
    result_b64 = data.get('image') or ''
    if not result_b64:
        raise RuntimeError(f'no image returned from Ollama — response keys: {list(data.keys())}')
    return base64.b64decode(result_b64)
