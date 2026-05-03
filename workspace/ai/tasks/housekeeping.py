"""Celery housekeeping tasks for the AI module."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name='ai.purge_ai_tasks', bind=True, max_retries=0)
def purge_ai_tasks(self):
    """Delete completed AI tasks older than ``AI_TASK_RETENTION_DAYS``."""
    from workspace.ai.models import AITask

    retention_days = getattr(settings, 'AI_TASK_RETENTION_DAYS', 90)
    cutoff = timezone.now() - timedelta(days=retention_days)

    qs = AITask.objects.filter(created_at__lte=cutoff)
    count = qs.count()

    if not count:
        logger.info('AI task purge: nothing to delete.')
        return {'deleted': 0, 'retention_days': retention_days}

    logger.info('AI task purge: deleting %d tasks older than %d days', count, retention_days)
    qs.delete()

    logger.info('AI task purge complete.')
    return {'deleted': count, 'retention_days': retention_days}
