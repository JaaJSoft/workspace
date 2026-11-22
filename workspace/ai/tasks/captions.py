"""Fire-and-forget AI captioning of chat image attachments.

Captions feed the conversation history builder: once an image falls out
of the pixel window, its ai_description is replayed as text so vision
bots keep a memory of it.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from pydantic_ai.messages import BinaryContent

from workspace.common.logging import scrub
from workspace.common.task_priority import LOW_PRIORITY

logger = logging.getLogger(__name__)

CAPTION_SYSTEM_PROMPT = (
    "You are an image captioning assistant. Describe the image in 1-2 short "
    "factual sentences: main subject, style, and any visible text. "
    "Reply with the description only."
)


@shared_task(priority=LOW_PRIORITY)
def generate_attachment_caption(attachment_uuid):
    """Caption one image attachment. Idempotent: no-op if already captioned."""
    from workspace.ai.prompts.base import prompt
    from workspace.ai.services.llm import run_prompt
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
    image = BinaryContent(data=data, media_type=att.mime_type)
    try:
        # No cap of its own: a reasoning model spends its tokens thinking
        # first, and one sized for two sentences leaves none for them.
        result = run_prompt(
            prompt(CAPTION_SYSTEM_PROMPT, [image]),
            model=settings.AI_VISION_MODEL or settings.AI_MODEL,
        )
    except Exception:
        logger.warning("Caption generation failed for %s", scrub(str(attachment_uuid)))
        return
    caption = result.output.strip()
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
