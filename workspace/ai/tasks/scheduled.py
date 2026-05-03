"""Celery wrappers for scheduled-message AI tasks."""

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name='ai.dispatch_scheduled_messages')
def dispatch_scheduled_messages():
    """Find due scheduled messages and dispatch a generation task for each."""
    from workspace.ai.models import ScheduledMessage

    now = timezone.now()
    due = ScheduledMessage.objects.filter(is_active=True, next_run_at__lte=now)
    count = 0
    for schedule in due:
        generate_scheduled_response.delay(str(schedule.uuid))
        count += 1
    if count:
        logger.info('Dispatched %d scheduled message(s)', count)


@shared_task(name='ai.generate_scheduled_response', bind=True, max_retries=0)
def generate_scheduled_response(self, schedule_id: str):
    """Generate a bot response for a scheduled message."""
    from workspace.ai.services.scheduled_response import generate_scheduled
    return generate_scheduled(schedule_id)
