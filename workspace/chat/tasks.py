"""Celery tasks for chat maintenance and link previews."""

import logging

from celery import shared_task

from .link_preview_service import fetch_opengraph
from .models import LinkPreview, Message, MessageLinkPreview
from .services import notify_conversation_members

logger = logging.getLogger(__name__)


@shared_task(name='chat.purge_orphan_attachments', bind=True, max_retries=0)
def purge_orphan_attachments(self):
    """Delete chat attachment files on disk with no matching DB row."""
    from django.core.management import call_command
    from io import StringIO

    out = StringIO()
    call_command('purge_orphan_attachments', stdout=out)
    result = out.getvalue().strip()
    logger.info("purge_orphan_attachments: %s", result)
    return result


@shared_task(name='chat.fetch_link_previews', ignore_result=True, soft_time_limit=60)
def fetch_link_previews(message_uuid: str, urls: list[str]):
    """Fetch OpenGraph metadata for URLs found in a chat message.

    Creates/reuses LinkPreview rows and links them to the message.
    Notifies conversation members via SSE when done.
    """
    try:
        message = Message.objects.select_related('conversation').get(pk=message_uuid)
    except Message.DoesNotExist:
        logger.warning('fetch_link_previews: message %s not found', message_uuid)
        return

    created_any = False

    for position, url in enumerate(urls):
        # Check for existing cached preview
        try:
            preview = LinkPreview.objects.get(url=url)
            if preview.fetch_failed:
                continue  # Don't retry previously failed URLs
            # Reuse cached preview
            MessageLinkPreview.objects.get_or_create(
                message=message,
                preview=preview,
                defaults={'position': position},
            )
            created_any = True
            continue
        except LinkPreview.DoesNotExist:
            pass

        # Fetch new preview
        preview = LinkPreview(url=url)
        try:
            meta = fetch_opengraph(url)
            preview.title = meta.get('title', '')[:500]
            preview.description = meta.get('description', '')[:2000]
            preview.image_url = meta.get('image', '')[:2048]
            preview.favicon_url = meta.get('favicon', '')[:500]
            preview.site_name = meta.get('site_name', '')[:200]
            preview.fetch_failed = False
            preview.save()
            MessageLinkPreview.objects.create(
                message=message,
                preview=preview,
                position=position,
            )
            created_any = True
        except Exception:
            logger.info('fetch_link_previews: failed to fetch %s', url, exc_info=True)
            preview.fetch_failed = True
            preview.save()

    if created_any:
        notify_conversation_members(message.conversation)
