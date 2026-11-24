"""Celery tasks for chat maintenance and link previews."""

import logging

from celery import shared_task

from workspace.common.logging import scrub
from workspace.common.task_priority import (
    BACKGROUND_PRIORITY,
    INTERACTIVE_PRIORITY,
    NORMAL_PRIORITY,
)

from .models import LinkPreview, Message, MessageLinkPreview
from .services.link_preview import fetch_opengraph
from .services.notifications import notify_conversation_members

logger = logging.getLogger(__name__)


@shared_task(
    name="chat.purge_orphan_attachments",
    priority=BACKGROUND_PRIORITY,
    bind=True,
    max_retries=0,
)
def purge_orphan_attachments(self):
    """Delete chat attachment files on disk with no matching DB row."""
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("purge_orphan_attachments", stdout=out)
    result = out.getvalue().strip()
    logger.info("purge_orphan_attachments: %s", result)
    return result


def _fetch_preview(url: str) -> LinkPreview:
    """Fetch ``url`` and store its preview, or reuse the row a concurrent task stored first."""
    try:
        meta = fetch_opengraph(url)
    except Exception:
        logger.info(
            "fetch_link_previews: failed to fetch %s", scrub(url), exc_info=True
        )
        fields = {"fetch_failed": True}
    else:
        fields = {
            "title": meta.get("title", "")[:500],
            "description": meta.get("description", "")[:2000],
            "image_url": meta.get("image", "")[:2048],
            "favicon_url": meta.get("favicon", "")[:500],
            "site_name": meta.get("site_name", "")[:200],
        }
    preview, _ = LinkPreview.objects.get_or_create(url=url, defaults=fields)
    return preview


@shared_task(
    name="chat.fetch_link_previews",
    priority=INTERACTIVE_PRIORITY,
    ignore_result=True,
    soft_time_limit=60,
)
def fetch_link_previews(message_uuid: str, urls: list[str]):
    """Fetch OpenGraph metadata for URLs found in a chat message.

    Creates/reuses LinkPreview rows and links them to the message.
    Notifies conversation members via SSE when done.
    """
    try:
        message = Message.objects.select_related("conversation").get(pk=message_uuid)
    except Message.DoesNotExist:
        logger.warning("fetch_link_previews: message %s not found", message_uuid)
        return

    created_any = False

    for position, url in enumerate(urls):
        preview = LinkPreview.objects.filter(url=url).first() or _fetch_preview(url)
        if preview.fetch_failed:
            continue  # Don't retry previously failed URLs
        MessageLinkPreview.objects.get_or_create(
            message=message,
            preview=preview,
            defaults={"position": position},
        )
        created_any = True

    if created_any:
        notify_conversation_members(message.conversation)


@shared_task(name="chat.end_stale_calls", priority=NORMAL_PRIORITY, ignore_result=True)
def end_stale_calls():
    """Reap call sessions whose participants all stopped sending heartbeats."""
    from .services.calls import end_stale_calls as _end_stale_calls

    count = _end_stale_calls()
    if count:
        logger.info("end_stale_calls: ended %s stale call(s)", count)
    return count
