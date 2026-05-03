"""Celery housekeeping tasks for the AI module."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name='ai.purge_ai_tasks', bind=True, max_retries=0)
def purge_ai_tasks(self):
    """Delete terminal AI tasks (COMPLETED/FAILED) older than
    ``AI_TASK_RETENTION_DAYS``.

    Filters on status + ``completed_at`` rather than ``created_at`` so a
    long-running PROCESSING task or a queued PENDING task can never be
    deleted in flight just because it was created a long time ago.
    """
    from workspace.ai.models import AITask

    retention_days = getattr(settings, 'AI_TASK_RETENTION_DAYS', 90)
    cutoff = timezone.now() - timedelta(days=retention_days)

    terminal_statuses = (AITask.Status.COMPLETED, AITask.Status.FAILED)
    qs = AITask.objects.filter(
        status__in=terminal_statuses,
        completed_at__lte=cutoff,
    )
    count = qs.count()

    if not count:
        logger.info('AI task purge: nothing to delete.')
        return {'deleted': 0, 'retention_days': retention_days}

    logger.info(
        'AI task purge: deleting %d terminal tasks completed more than %d days ago',
        count, retention_days,
    )
    qs.delete()

    logger.info('AI task purge complete.')
    return {'deleted': count, 'retention_days': retention_days}
