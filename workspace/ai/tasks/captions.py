"""Fire-and-forget AI captioning of chat image attachments.

Captions feed the conversation history builder: once an image falls out
of the pixel window, its ai_description is replayed as text so vision
bots keep a memory of it.
"""

import base64
import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache

from workspace.common.logging import scrub

logger = logging.getLogger(__name__)

CAPTION_SYSTEM_PROMPT = (
    "You are an image captioning assistant. Describe the image in 1-2 short "
    "factual sentences: main subject, style, and any visible text. "
    "Reply with the description only."
)


@shared_task
def generate_attachment_caption(attachment_uuid):
    """Caption one image attachment. Idempotent: no-op if already captioned."""
    from workspace.ai.services.llm import call_llm
    from workspace.chat.models import MessageAttachment

    try:
        att = MessageAttachment.objects.get(uuid=attachment_uuid)
    except MessageAttachment.DoesNotExist:
        return
    if att.ai_description or not att.is_image:
        return
    try:
        data = att.file.read()
    except FileNotFoundError, OSError:
        logger.warning(
            "Caption: could not read attachment %s", scrub(str(attachment_uuid))
        )
        return
    b64 = base64.b64encode(data).decode()
    messages = [
        {"role": "system", "content": CAPTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{att.mime_type};base64,{b64}"},
                }
            ],
        },
    ]
    try:
        result = call_llm(
            messages,
            model=settings.AI_VISION_MODEL or settings.AI_MODEL,
            max_tokens=150,
        )
    except Exception:
        logger.warning("Caption generation failed for %s", scrub(str(attachment_uuid)))
        return
    caption = (result.get("content") or "").strip()
    if caption:
        MessageAttachment.objects.filter(uuid=att.uuid).update(ai_description=caption)


def enqueue_caption_if_image(attachment):
    """Enqueue captioning for an image attachment; no-op otherwise."""
    if not settings.AI_API_KEY:
        return
    if not attachment.is_image:
        return
    generate_attachment_caption.delay(str(attachment.uuid))


def enqueue_caption_retry(attachment):
    """History-build re-enqueue, throttled to one attempt per attachment per hour."""
    if not settings.AI_API_KEY or not attachment.is_image:
        return
    if not cache.add(f"ai:caption-enqueued:{attachment.uuid}", 1, timeout=3600):
        return
    generate_attachment_caption.delay(str(attachment.uuid))
