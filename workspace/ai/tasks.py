"""Celery task entrypoints for AI work.

Each task here is a thin wrapper that delegates to a service module under
``workspace/ai/services/``. The wrappers stay in this file so Celery's
autodiscover keeps registering the same task names.

The task bodies live in services so the logic can be exercised in tests
without going through the Celery harness.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ── Bot conversation tasks ────────────────────────────────────────


@shared_task(name='ai.generate_chat_response', bind=True, max_retries=0)
def generate_chat_response(self, conversation_id: str, message_id: str, bot_user_id: int):
    """Generate a bot response in a chat conversation."""
    from workspace.ai.services.chat_response import generate_response
    return generate_response(conversation_id, message_id, bot_user_id)


@shared_task(name='ai.update_conversation_summary', bind=True, max_retries=0)
def update_conversation_summary(self, conversation_id: str):
    """Update the rolling summary for a bot conversation."""
    from workspace.ai.services.chat_summary import update_summary
    return update_summary(conversation_id)


@shared_task(name='ai.generate_conversation_title', bind=True, max_retries=0)
def generate_conversation_title(self, conversation_id: str):
    """Generate a short title for a bot conversation based on the first exchange."""
    from workspace.ai.services.chat_title import generate_title
    return generate_title(conversation_id)


# ── Scheduled message tasks ───────────────────────────────────────


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


# ── Mail AI tasks ─────────────────────────────────────────────────


@shared_task(name='ai.summarize', bind=True, max_retries=0)
def summarize(self, task_id: str):
    """Summarize a mail message."""
    from workspace.ai.services.mail_summary import summarize_mail
    return summarize_mail(task_id)


@shared_task(name='ai.compose_email', bind=True, max_retries=0)
def compose_email(self, task_id: str):
    """Compose or reply to an email."""
    from workspace.ai.services.mail_compose import compose_mail
    return compose_mail(task_id)


@shared_task(name='ai.classify_mail', bind=True, max_retries=0)
def classify_mail_messages(self, task_id: str):
    """Classify mail messages by assigning user-defined labels."""
    from workspace.ai.services.mail_classifier import classify_mail
    return classify_mail(task_id)


# ── Editor AI tasks ───────────────────────────────────────────────


@shared_task(name='ai.editor_action', bind=True, max_retries=0)
def editor_action(self, task_id: str):
    """Run an AI action on editor content (improve, explain, summarize, custom)."""
    from workspace.ai.services.editor import run_editor_action
    return run_editor_action(task_id)


# ── Housekeeping ──────────────────────────────────────────────────


@shared_task(name='ai.purge_ai_tasks', bind=True, max_retries=0)
def purge_ai_tasks(self):
    """Delete completed AI tasks older than AI_TASK_RETENTION_DAYS."""
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
