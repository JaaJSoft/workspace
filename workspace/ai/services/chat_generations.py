"""Which bot replies are under way, as the chat UI should see them."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from workspace.ai.models import AITask


def active_chat_tasks():
    """Chat AITasks still queued or running.

    A row only leaves these states when its worker closes it, so a worker
    killed at its hard time limit, or a queue message lost by the broker,
    would otherwise read as generating forever - and the chat keeps its
    typing bubble up and its composer locked for as long as that lasts.
    Nothing outlives ``CELERY_TASK_TIME_LIMIT`` counted from the dispatch,
    which is also an upper bound on any genuine run.
    """
    cutoff = timezone.now() - timedelta(seconds=settings.CELERY_TASK_TIME_LIMIT)
    return AITask.objects.filter(
        task_type=AITask.TaskType.CHAT,
        status__in=[AITask.Status.PENDING, AITask.Status.PROCESSING],
        created_at__gte=cutoff,
    )
