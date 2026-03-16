"""Celery tasks for chat maintenance."""

import logging

from celery import shared_task

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
